"""LLM policy backends: vLLM for throughput, HuggingFace for a plain forward pass.

Both render prompts with the model's own chat template through
`training.chat_format`, so what the policy sees at rollout time is byte-identical
to what it was trained on.

`repair_attempts` re-asks once (or more) when output cannot be parsed. It
defaults to 0: evaluation must be able to *measure* the malformed rate, and a
silently repaired rollout hides exactly the failure mode this project tracks.
Turn it on for data collection, leave it off for benchmarks.
"""

from __future__ import annotations

from typing import Any, Sequence

from ..chat_format import RESPONSE_FORMAT, parse_action
from .base import BasePolicy, GenerationConfig, Messages

REPAIR_HINT = (
    "Your previous reply could not be parsed. Reply with exactly one JSON object and nothing else, "
    f"in this form: {RESPONSE_FORMAT}"
)


def render_prompt(tokenizer: Any, messages: Messages, enable_thinking: bool = False) -> str:
    """Chat template -> prompt string, with the generation prompt appended."""
    kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": True}
    template = getattr(tokenizer, "chat_template", None) or ""
    if "enable_thinking" in template:
        kwargs["enable_thinking"] = enable_thinking
    return tokenizer.apply_chat_template(list(messages), **kwargs)


class _RepairMixin:
    """Shared malformed-output retry loop."""

    generation: GenerationConfig
    repair_attempts: int = 0

    def _generate(self, batch: Sequence[Messages]) -> list[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    def act_batch(self, batch: Sequence[Messages]) -> list[str]:
        outputs = self._generate(batch)
        for _ in range(self.repair_attempts):
            broken = [index for index, text in enumerate(outputs) if parse_action(text).malformed]
            if not broken:
                break
            retry_batch = [[*batch[index], {"role": "user", "content": REPAIR_HINT}] for index in broken]
            for index, text in zip(broken, self._generate(retry_batch)):
                outputs[index] = text
        return outputs


class VLLMPolicy(_RepairMixin, BasePolicy):
    """vLLM backend. The rollout path for PPO / OPD / evaluation."""

    name = "vllm"

    def __init__(
        self,
        model_path: str,
        generation: GenerationConfig | None = None,
        *,
        tensor_parallel_size: int = 1,
        gpu_memory_utilization: float = 0.85,
        max_model_len: int = 8192,
        repair_attempts: int = 0,
        engine: Any = None,
        tokenizer: Any = None,
    ) -> None:
        from transformers import AutoTokenizer  # local: keeps the module importable without transformers

        self.model_path = model_path
        self.generation = generation or GenerationConfig()
        self.repair_attempts = repair_attempts
        self.tokenizer = tokenizer or AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        if engine is not None:
            self.engine = engine
        else:
            from vllm import LLM

            self.engine = LLM(
                model=model_path,
                tensor_parallel_size=tensor_parallel_size,
                gpu_memory_utilization=gpu_memory_utilization,
                max_model_len=max_model_len,
                trust_remote_code=True,
            )

    def _sampling_params(self) -> Any:
        from vllm import SamplingParams

        config = self.generation
        return SamplingParams(
            n=1,
            temperature=config.temperature,
            top_p=config.top_p,
            top_k=config.top_k if config.top_k > 0 else -1,
            max_tokens=config.max_new_tokens,
            repetition_penalty=config.repetition_penalty,
            stop=config.stop or None,
            seed=config.seed,
        )

    def _generate(self, batch: Sequence[Messages]) -> list[str]:
        prompts = [render_prompt(self.tokenizer, messages, self.generation.enable_thinking) for messages in batch]
        outputs = self.engine.generate(prompts, self._sampling_params())
        return [output.outputs[0].text.strip() for output in outputs]

    def sync_weights_from(self, state_dict: dict[str, Any]) -> None:
        """Refresh the rollout engine after a policy update (PPO/OPD inner loop)."""
        worker = self.engine.llm_engine.model_executor.driver_worker
        worker.model_runner.model.load_weights(state_dict.items())


class HFPolicy(_RepairMixin, BasePolicy):
    """HuggingFace backend: slower, no extra service, useful for smoke tests and CPU runs."""

    name = "hf"

    def __init__(
        self,
        model_path: str,
        generation: GenerationConfig | None = None,
        *,
        device: str = "auto",
        dtype: str = "bfloat16",
        batch_size: int = 8,
        repair_attempts: int = 0,
        model: Any = None,
        tokenizer: Any = None,
    ) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model_path = model_path
        self.generation = generation or GenerationConfig()
        self.repair_attempts = repair_attempts
        self.batch_size = batch_size
        self.tokenizer = tokenizer or AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"  # required for correct batched generation
        self.model = model or AutoModelForCausalLM.from_pretrained(
            model_path,
            torch_dtype=getattr(torch, dtype, torch.float32),
            device_map=device,
            trust_remote_code=True,
        )
        self.model.eval()

    def _generate(self, batch: Sequence[Messages]) -> list[str]:
        import torch

        results: list[str] = []
        config = self.generation
        for start in range(0, len(batch), self.batch_size):
            chunk = batch[start : start + self.batch_size]
            prompts = [render_prompt(self.tokenizer, messages, config.enable_thinking) for messages in chunk]
            encoded = self.tokenizer(prompts, return_tensors="pt", padding=True, add_special_tokens=False)
            encoded = {key: value.to(self.model.device) for key, value in encoded.items()}
            with torch.no_grad():
                generated = self.model.generate(
                    **encoded,
                    max_new_tokens=config.max_new_tokens,
                    do_sample=not config.greedy,
                    temperature=config.temperature if not config.greedy else None,
                    top_p=config.top_p if not config.greedy else None,
                    pad_token_id=self.tokenizer.pad_token_id,
                )
            prompt_length = encoded["input_ids"].shape[1]
            results.extend(
                text.strip()
                for text in self.tokenizer.batch_decode(generated[:, prompt_length:], skip_special_tokens=True)
            )
        return results


def load_policy(spec: str, generation: GenerationConfig | None = None, **kwargs: Any) -> BasePolicy:
    """Build a policy from a CLI string.

        expert              scripted oracle (data generation only)
        rules               hand-written rule baseline
        rules:full          rule baseline with every recovery rule enabled
        hf:<model_path>     HuggingFace backend
        vllm:<model_path>   vLLM backend
    """
    from .scripted_baseline import ExpertPolicy, RuleBaselinePolicy, RuleCoverage

    if spec == "expert":
        return ExpertPolicy()
    if spec.startswith("rules"):
        _, _, variant = spec.partition(":")
        return RuleBaselinePolicy(RuleCoverage.full() if variant == "full" else RuleCoverage())
    backend, _, path = spec.partition(":")
    if not path:
        raise ValueError(f"policy spec {spec!r} needs a model path, e.g. vllm:/models/qwen3-8b")
    if backend == "vllm":
        return VLLMPolicy(path, generation, **kwargs)
    if backend == "hf":
        return HFPolicy(path, generation, **kwargs)
    raise ValueError(f"unknown policy backend {backend!r}")


__all__ = ["HFPolicy", "REPAIR_HINT", "VLLMPolicy", "load_policy", "render_prompt"]
