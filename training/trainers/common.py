"""Shared wiring for the online trainers: DeepSpeed setup, model loading, logging.

Kept apart from the loss functions so that `ppo.py` and `opd.py` stay importable
and testable without torch, deepspeed or a GPU.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ..config import ModelConfig, OptimConfig, RunConfig


def resolve_deepspeed_config(
    path: str | Path,
    *,
    micro_batch_size: int,
    gradient_accumulation: int,
    world_size: int,
    gradient_clipping: float,
    hidden_size: int,
) -> dict[str, Any]:
    """Load a DeepSpeed JSON and replace every "auto" with a concrete value.

    The shipped configs use "auto" because HuggingFace's Trainer fills those in.
    The online trainers call `deepspeed.initialize` directly, which does not, so
    the same files are resolved here using the same rules Trainer applies.
    """
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    config["train_micro_batch_size_per_gpu"] = micro_batch_size
    config["gradient_accumulation_steps"] = gradient_accumulation
    config["train_batch_size"] = micro_batch_size * gradient_accumulation * max(world_size, 1)
    config["gradient_clipping"] = gradient_clipping

    zero = config.get("zero_optimization", {})
    if zero.get("reduce_bucket_size") == "auto":
        zero["reduce_bucket_size"] = hidden_size * hidden_size
    if zero.get("allgather_bucket_size") == "auto":
        zero["allgather_bucket_size"] = hidden_size * hidden_size
    if zero.get("stage3_prefetch_bucket_size") == "auto":
        zero["stage3_prefetch_bucket_size"] = int(0.9 * hidden_size * hidden_size)
    if zero.get("stage3_param_persistence_threshold") == "auto":
        zero["stage3_param_persistence_threshold"] = 10 * hidden_size
    config["zero_optimization"] = zero
    return config


def load_causal_lm(model_config: ModelConfig, path: str | None = None, *, trainable: bool = True) -> Any:
    import torch
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(
        path or model_config.path,
        torch_dtype=getattr(torch, model_config.dtype, torch.bfloat16),
        attn_implementation=model_config.attn_implementation,
        trust_remote_code=model_config.trust_remote_code,
    )
    if trainable:
        if model_config.gradient_checkpointing:
            model.gradient_checkpointing_enable()
            model.config.use_cache = False
    else:
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
    return model


def load_tokenizer(model_config: ModelConfig, path: str | None = None) -> Any:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(path or model_config.path, trust_remote_code=model_config.trust_remote_code)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def build_optimizer(model: Any, optim: OptimConfig) -> Any:
    import torch

    decay, no_decay = [], []
    for name, parameter in model.named_parameters():
        if not parameter.requires_grad:
            continue
        (no_decay if parameter.ndim == 1 or name.endswith(".bias") else decay).append(parameter)
    groups = [
        {"params": decay, "weight_decay": optim.weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]
    return torch.optim.AdamW(groups, lr=optim.lr, betas=(optim.beta1, optim.beta2), eps=optim.eps)


def initialize_engine(model: Any, optim: OptimConfig, run: RunConfig, hidden_size: int) -> tuple[Any, Any]:
    """DeepSpeed engine + optimizer, or plain torch when no DeepSpeed config is set."""
    optimizer = build_optimizer(model, optim)
    if not run.deepspeed:
        return model, optimizer

    import deepspeed
    import torch.distributed as distributed

    world_size = distributed.get_world_size() if distributed.is_initialized() else 1
    config = resolve_deepspeed_config(
        run.deepspeed,
        micro_batch_size=run.micro_batch_size,
        gradient_accumulation=run.gradient_accumulation,
        world_size=world_size,
        gradient_clipping=optim.max_grad_norm,
        hidden_size=hidden_size,
    )
    engine, engine_optimizer, _, _ = deepspeed.initialize(model=model, optimizer=optimizer, config=config)
    return engine, engine_optimizer


def hidden_size_of(model: Any) -> int:
    config = getattr(model, "config", None)
    return int(getattr(config, "hidden_size", 4096)) if config is not None else 4096


def is_main_process() -> bool:
    try:
        import torch.distributed as distributed

        return not distributed.is_initialized() or distributed.get_rank() == 0
    except Exception:  # pragma: no cover - torch may be absent in tooling contexts
        return True


@dataclass
class MetricLogger:
    """Appends one JSON line per iteration and echoes a compact summary.

    A run's history therefore survives in a file next to its checkpoints, which is
    what later regression comparisons read.
    """

    path: Path
    keys: Sequence[str] = ()
    started: float = 0.0

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.started = time.time()

    def log(self, step: int, metrics: dict[str, Any]) -> None:
        if not is_main_process():
            return
        payload = {"step": step, "elapsed_sec": round(time.time() - self.started, 2), **metrics}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        shown = self.keys or [key for key in metrics if not key.startswith("_")][:8]
        parts = [f"{key}={_format(metrics[key])}" for key in shown if key in metrics]
        print(f"[{step:>5}] " + "  ".join(parts))


def _format(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def save_checkpoint(engine: Any, tokenizer: Any, directory: str | Path, tag: str) -> Path:
    """Save a rollout-ready checkpoint (ZeRO-3 weights gathered to fp16/bf16)."""
    target = Path(directory) / tag
    target.mkdir(parents=True, exist_ok=True)
    model = getattr(engine, "module", engine)
    if hasattr(engine, "save_16bit_model"):
        engine.save_16bit_model(str(target))
    else:
        model.save_pretrained(str(target))
    if is_main_process() and tokenizer is not None:
        tokenizer.save_pretrained(str(target))
    return target


__all__ = [
    "MetricLogger",
    "build_optimizer",
    "hidden_size_of",
    "initialize_engine",
    "is_main_process",
    "load_causal_lm",
    "load_tokenizer",
    "resolve_deepspeed_config",
    "save_checkpoint",
]
