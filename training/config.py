"""Typed configs for every training stage, loadable from YAML.

One dataclass tree per stage, so a run is fully described by a file that can be
committed next to its results. `--set a.b=c` overrides land in the same tree and
are echoed into the saved config, which means a rerun of a logged experiment
does not depend on remembering which flags were typed.

Values are coerced to the dataclass field types, and unknown keys are an error
rather than a silent no-op -- a typo in a YAML key must not quietly train with
the default learning rate.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

T = TypeVar("T")


# ----------------------------------------------------------------- components


@dataclass
class ModelConfig:
    path: str = "Qwen/Qwen3-8B"
    dtype: str = "bfloat16"
    attn_implementation: str = "flash_attention_2"
    gradient_checkpointing: bool = True
    trust_remote_code: bool = True
    max_seq_len: int = 8192
    #: Full-parameter post-training is the project's setting; LoRA is a fallback
    #: for single-GPU debugging, never for a reported number.
    use_lora: bool = False
    lora_rank: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.05


@dataclass
class DataConfig:
    train_path: str = "data/sft/train.jsonl"
    val_path: str = "data/sft/val.jsonl"
    max_samples: int | None = None
    shuffle: bool = True


@dataclass
class OptimConfig:
    lr: float = 1e-5
    weight_decay: float = 0.0
    warmup_ratio: float = 0.03
    scheduler: str = "cosine"
    beta1: float = 0.9
    beta2: float = 0.95
    eps: float = 1e-8
    max_grad_norm: float = 1.0


@dataclass
class RunConfig:
    output_dir: str = "runs/exp"
    seed: int = 42
    epochs: float = 2.0
    micro_batch_size: int = 1
    gradient_accumulation: int = 16
    log_every: int = 10
    eval_every: int = 200
    save_every: int = 500
    deepspeed: str | None = "training/configs/deepspeed/zero3_bf16.json"
    report_to: str = "none"  # none | wandb | tensorboard
    run_name: str = ""


@dataclass
class RolloutConfig:
    backend: str = "vllm"  # vllm | hf
    split: str = "train"
    episodes_per_iter: int = 64
    concurrency: int = 32
    temperature: float = 1.0
    top_p: float = 0.95
    max_new_tokens: int = 256
    max_history_turns: int | None = None
    max_turns: int | None = None
    repair_attempts: int = 0
    gpu_memory_utilization: float = 0.60
    tensor_parallel_size: int = 1


@dataclass
class RewardSettings:
    preset: str = "progress"
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class CreditSettings:
    gamma: float = 0.99
    lam: float = 0.95
    broadcast: str = "uniform"  # uniform | last_token | first_token
    whiten_advantages: bool = True
    #: The ablation switch behind "process reward != credit assignment": when
    #: True, one episode scalar is spread over every action token instead.
    terminal_broadcast: bool = False


# --------------------------------------------------------------------- stages


@dataclass
class SFTConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    data: DataConfig = field(default_factory=DataConfig)
    optim: OptimConfig = field(default_factory=OptimConfig)
    run: RunConfig = field(default_factory=RunConfig)
    #: Loss is computed on assistant spans only; observations are never targets.
    mask_observations: bool = True
    packing: bool = False


@dataclass
class DPOConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    data: DataConfig = field(default_factory=lambda: DataConfig("data/dpo/train.jsonl", "data/dpo/val.jsonl"))
    optim: OptimConfig = field(default_factory=lambda: OptimConfig(lr=5e-7))
    run: RunConfig = field(default_factory=lambda: RunConfig(output_dir="runs/dpo", epochs=1.0))
    beta: float = 0.1
    loss_type: str = "sigmoid"
    label_smoothing: float = 0.0
    reference_path: str | None = None  # defaults to the SFT checkpoint being trained from
    max_prompt_len: int = 6144


@dataclass
class PPOConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    optim: OptimConfig = field(default_factory=lambda: OptimConfig(lr=5e-7, warmup_ratio=0.0))
    run: RunConfig = field(default_factory=lambda: RunConfig(output_dir="runs/ppo", epochs=1.0))
    rollout: RolloutConfig = field(default_factory=RolloutConfig)
    reward: RewardSettings = field(default_factory=RewardSettings)
    credit: CreditSettings = field(default_factory=CreditSettings)
    iterations: int = 200
    ppo_epochs: int = 1
    clip_ratio: float = 0.2
    value_clip: float = 0.2
    vf_coef: float = 0.5
    entropy_coef: float = 0.0
    kl_coef: float = 0.02
    kl_target: float | None = None
    critic_mode: str = "shared"  # shared | separate | none
    critic_path: str | None = None
    reference_path: str | None = None
    normalize_rewards: bool = False
    save_every_iters: int = 20
    eval_every_iters: int = 20
    eval_episodes: int = 100


@dataclass
class TeacherSettings:
    path: str = "Qwen/Qwen3-30B-A3B"
    dtype: str = "bfloat16"
    tensor_parallel_size: int = 1
    #: Gate: which turns are worth a teacher forward pass.
    on_error: bool = True
    on_safety_event: bool = True
    no_progress_threshold: int = 2
    entropy_threshold: float = 1.0
    disagreement_threshold: float = 0.5
    random_sample_prob: float = 0.05
    max_scored_fraction: float = 0.5
    max_scored_turns: int | None = None
    #: Per-turn weighting.
    base_weight: float = 1.0
    uncertainty_scale: float = 0.5
    disagreement_scale: float = 0.5
    max_weight: float = 3.0


@dataclass
class OPDMixConfig:
    opd_start: float = 1.0
    opd_end: float = 0.1
    env_start: float = 0.2
    env_end: float = 1.0
    warmup_fraction: float = 0.2
    mode: str = "cosine"


@dataclass
class OPDTrainConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    optim: OptimConfig = field(default_factory=lambda: OptimConfig(lr=5e-7, warmup_ratio=0.0))
    run: RunConfig = field(default_factory=lambda: RunConfig(output_dir="runs/opd", epochs=1.0))
    rollout: RolloutConfig = field(default_factory=RolloutConfig)
    reward: RewardSettings = field(default_factory=RewardSettings)
    credit: CreditSettings = field(default_factory=CreditSettings)
    teacher: TeacherSettings = field(default_factory=TeacherSettings)
    mix: OPDMixConfig = field(default_factory=OPDMixConfig)
    ppo: PPOConfig = field(default_factory=PPOConfig)
    iterations: int = 200
    #: direct_kl  per-token reverse KL on the student's sampled tokens (pure OPD)
    #: reward     teacher signal enters as a dense per-turn reward through GAE
    opd_loss: str = "direct_kl"
    #: False = pure OPD (no environment reward at all).
    use_environment_reward: bool = True
    save_every_iters: int = 20
    eval_every_iters: int = 20
    eval_episodes: int = 100


STAGES: dict[str, type] = {
    "sft": SFTConfig,
    "dpo": DPOConfig,
    "ppo": PPOConfig,
    "opd": OPDTrainConfig,
}


# --------------------------------------------------------------------- loading


def _coerce(value: Any, annotation: Any) -> Any:
    if annotation in (Any, None) or value is None:
        return value
    origin = get_origin(annotation)
    if origin is not None:  # Optional[...] / list[...] / dict[...]
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if origin in (list, dict, tuple, set):
            return value
        if len(args) == 1:
            return _coerce(value, args[0])
        return value
    if is_dataclass(annotation) and isinstance(value, dict):
        return from_dict(annotation, value)
    if annotation is bool and isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if annotation in (int, float, str) and not isinstance(value, annotation):
        return annotation(value)
    return value


def from_dict(cls: type[T], data: dict[str, Any]) -> T:
    """Build a (possibly nested) dataclass, rejecting unknown keys."""
    if not is_dataclass(cls):  # pragma: no cover - defensive
        raise TypeError(f"{cls} is not a dataclass")
    known = {item.name for item in fields(cls)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"unknown config keys for {cls.__name__}: {unknown}")
    # `from __future__ import annotations` makes field.type a string, so resolve
    # the real annotations before coercing anything.
    hints = get_type_hints(cls)
    kwargs = {name: _coerce(value, hints.get(name)) for name, value in data.items()}
    return cls(**kwargs)  # type: ignore[return-value]


def to_dict(config: Any) -> dict[str, Any]:
    return dataclasses.asdict(config)


def apply_overrides(data: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """Apply `a.b=c` strings onto a nested dict, parsing values as YAML scalars."""
    import yaml

    for override in overrides:
        if "=" not in override:
            raise ValueError(f"override {override!r} must look like key.path=value")
        path, _, raw = override.partition("=")
        node = data
        parts = path.strip().split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError(f"override path {path!r} traverses a non-mapping value")
        node[parts[-1]] = yaml.safe_load(raw)
    return data


def load_config(stage: str, path: str | Path | None = None, overrides: list[str] | None = None) -> Any:
    """Load `stage` config from YAML (or defaults) with `key.path=value` overrides."""
    import yaml

    cls = STAGES[stage]
    data: dict[str, Any] = {}
    if path:
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must contain a mapping")
        data = loaded
        data.pop("stage", None)  # allow a documentation field in the file
    if overrides:
        data = apply_overrides(data, list(overrides))
    return from_dict(cls, data)


def save_config(config: Any, directory: str | Path, filename: str = "config.json") -> Path:
    """Write the resolved config next to the checkpoints it produced."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / filename
    target.write_text(json.dumps(to_dict(config), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return target


def add_config_args(parser: Any, stage: str) -> Any:
    parser.add_argument("--config", type=str, default=None, help=f"YAML config for the {stage} stage.")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    return parser


__all__ = [
    "CreditSettings",
    "DPOConfig",
    "DataConfig",
    "ModelConfig",
    "OPDMixConfig",
    "OPDTrainConfig",
    "OptimConfig",
    "PPOConfig",
    "RewardSettings",
    "RolloutConfig",
    "RunConfig",
    "SFTConfig",
    "STAGES",
    "TeacherSettings",
    "add_config_args",
    "apply_overrides",
    "from_dict",
    "load_config",
    "save_config",
    "to_dict",
]
