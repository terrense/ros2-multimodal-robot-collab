"""GPU memory arithmetic for the online trainers.

The rollout stage is *colocated*: one process holds the actor under DeepSpeed
ZeRO-3, a full copy of the reference model, and an in-process vLLM engine for
the rollout. Three models' worth of weights share one card, and the part that
is easiest to get wrong is the vLLM budget -- `gpu_memory_utilization` is a
fraction of *total* card memory, while the training state has already been
allocated by the time the engine starts. A value that looks generous on paper
is an OOM in practice.

Nothing here touches a GPU. It is arithmetic on numbers the configs declare,
so it can be tested on a laptop, and it is used to fail fast before a
multi-hour run dies in an OOM instead of after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GIB = 1024**3


class InsufficientRolloutMemory(RuntimeError):
    """Raised when the rollout engine cannot fit next to the training state."""


@dataclass(frozen=True)
class ModelFootprint:
    """What one model costs, in bytes.

    `params_billion` is an approximation used for planning before anything is
    loaded. The authoritative check is the free-memory probe in
    `check_rollout_budget`, which measures the card instead of guessing.
    """

    params_billion: float
    dtype_bytes: int = 2  # bf16
    optimizer_bytes_per_param: int = 8  # fp32 AdamW moments
    master_bytes_per_param: int = 0  # fp32 master copy, when one is kept

    @property
    def parameter_count(self) -> int:
        return int(self.params_billion * 1e9)

    @property
    def weights_bytes(self) -> int:
        return self.parameter_count * self.dtype_bytes

    @property
    def gradients_bytes(self) -> int:
        return self.parameter_count * self.dtype_bytes

    @property
    def optimizer_bytes(self) -> int:
        return self.parameter_count * self.optimizer_bytes_per_param

    @property
    def master_bytes(self) -> int:
        return self.parameter_count * self.master_bytes_per_param


def zero3_shard_bytes(total_bytes: int, gpus: int) -> int:
    """What one rank holds of a ZeRO-3 partitioned model."""
    return total_bytes // max(1, gpus)


def training_resident_bytes(
    actor: ModelFootprint,
    gpus: int,
    *,
    reference: ModelFootprint | None = None,
    keep_master_weights: bool = False,
) -> int:
    """Per-GPU memory the training process holds before the rollout engine starts.

    The actor is partitioned: weights, gradients and optimizer state are split
    across ranks. The reference model is not -- `load_causal_lm(...,
    trainable=False)` puts a full copy on the current device, so every rank
    carries one. That asymmetry is the reason a 16.4 GB reference shows up on
    every card of a 4-GPU run.
    """
    partitioned = actor.weights_bytes + actor.gradients_bytes + actor.optimizer_bytes
    per_gpu = zero3_shard_bytes(partitioned, gpus)
    if keep_master_weights:
        per_gpu += zero3_shard_bytes(actor.master_bytes, gpus)
    if reference is not None:
        per_gpu += reference.weights_bytes
    return per_gpu


def kv_bytes_per_sequence(
    model_config: Any,
    *,
    seq_len: int,
    dtype_bytes: int = 2,
) -> int:
    """KV cache one full-length sequence needs.

    Read off the model's own config so it tracks whatever checkpoint is loaded
    instead of a hard-coded architecture.
    """
    layers = int(getattr(model_config, "num_hidden_layers", 0) or 0)
    hidden = int(getattr(model_config, "hidden_size", 0) or 0)
    heads = int(getattr(model_config, "num_attention_heads", 0) or 0)
    kv_heads = int(getattr(model_config, "num_key_value_heads", 0) or heads)
    if not layers or not hidden or not heads:
        return 0
    head_dim = hidden // heads
    per_token = 2 * kv_heads * head_dim * dtype_bytes * layers  # K and V
    return per_token * seq_len


def kv_cache_bytes(
    model_config: Any,
    *,
    seq_len: int,
    concurrency: int,
    dtype_bytes: int = 2,
) -> int:
    """KV cache the rollout engine needs for `concurrency` sequences."""
    return kv_bytes_per_sequence(model_config, seq_len=seq_len, dtype_bytes=dtype_bytes) * max(1, concurrency)


def parameter_bytes(model: Any) -> int:
    """Exact size of a loaded model's parameters, in bytes."""
    try:
        return sum(parameter.numel() * parameter.element_size() for parameter in model.parameters())
    except Exception:  # pragma: no cover - a model without parameters
        return 0


@dataclass(frozen=True)
class RolloutBudget:
    """Whether the in-process rollout engine fits beside the training state."""

    total_bytes: int
    resident_bytes: int
    model_bytes: int
    kv_bytes_per_sequence: int
    concurrency: int
    requested_utilization: float

    @property
    def free_bytes(self) -> int:
        return max(0, self.total_bytes - self.resident_bytes)

    @property
    def requested_bytes(self) -> int:
        return int(self.total_bytes * self.requested_utilization)

    @property
    def kv_cache_needed_bytes(self) -> int:
        return self.kv_bytes_per_sequence * max(1, self.concurrency)

    @property
    def kv_cache_available_bytes(self) -> int:
        return max(0, self.requested_bytes - self.model_bytes)

    @property
    def max_concurrency(self) -> int:
        if not self.kv_bytes_per_sequence:
            return self.concurrency
        return self.kv_cache_available_bytes // self.kv_bytes_per_sequence

    @property
    def max_utilization(self) -> float:
        if not self.total_bytes:
            return 0.0
        return self.free_bytes / self.total_bytes

    @property
    def fits(self) -> bool:
        return self.requested_bytes <= self.free_bytes

    @property
    def holds_concurrency(self) -> bool:
        """Does the KV cache hold `concurrency` full-length sequences?"""
        return self.kv_cache_available_bytes >= self.kv_cache_needed_bytes

    def summary(self) -> str:
        return (
            f"rollout budget: card {self.total_bytes / GIB:.1f} GiB, training state "
            f"{self.resident_bytes / GIB:.1f} GiB, free {self.free_bytes / GIB:.1f} GiB; "
            f"engine asks {self.requested_utilization:.2f} "
            f"({self.requested_bytes / GIB:.1f} GiB) for weights {self.model_bytes / GIB:.1f} GiB "
            f"+ KV cache {self.kv_cache_available_bytes / GIB:.1f} GiB; "
            f"maximum that fits is {self.max_utilization:.2f}"
        )

    def concurrency_note(self) -> str:
        return (
            f"the KV cache holds {self.max_concurrency} full-length sequences "
            f"({self.concurrency} configured, "
            f"{self.kv_cache_needed_bytes / GIB:.1f} GiB needed at the configured concurrency)"
        )


def plan_rollout_budget(
    *,
    total_bytes: int,
    resident_bytes: int,
    model_bytes: int,
    kv_bytes_per_sequence: int,
    concurrency: int,
    requested_utilization: float,
) -> RolloutBudget:
    """Lay the card out: training state here, rollout engine there."""
    return RolloutBudget(
        total_bytes=total_bytes,
        resident_bytes=resident_bytes,
        model_bytes=model_bytes,
        kv_bytes_per_sequence=kv_bytes_per_sequence,
        concurrency=concurrency,
        requested_utilization=requested_utilization,
    )


def check_rollout_budget(budget: RolloutBudget) -> RolloutBudget:
    """Return the budget when it fits; raise with the fix when it does not."""
    if not budget.fits:
        raise InsufficientRolloutMemory(
            "the rollout engine does not fit next to the training state.\n"
            f"  {budget.summary()}\n"
            f"  Fix: lower rollout.gpu_memory_utilization to {budget.max_utilization:.2f} or below, "
            "or lower rollout.concurrency so the KV cache shrinks."
        )
    if budget.kv_bytes_per_sequence and budget.max_concurrency < 1:
        raise InsufficientRolloutMemory(
            "the rollout engine has no room for a KV cache.\n"
            f"  {budget.summary()}\n"
            "  Fix: lower rollout.concurrency, lower rollout.max_new_tokens, or give the engine "
            "a larger rollout.gpu_memory_utilization."
        )
    return budget


__all__ = [
    "GIB",
    "InsufficientRolloutMemory",
    "ModelFootprint",
    "RolloutBudget",
    "check_rollout_budget",
    "kv_bytes_per_sequence",
    "kv_cache_bytes",
    "parameter_bytes",
    "plan_rollout_budget",
    "training_resident_bytes",
    "zero3_shard_bytes",
]
