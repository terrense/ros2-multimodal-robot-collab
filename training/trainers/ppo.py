"""Step-level PPO: clipped policy loss, value loss, KL penalty.

Everything is masked to action tokens. Observation and system tokens are context
the environment produced, never something the policy is credited or blamed for,
so they must not enter any mean in this file.

The losses are plain tensor functions with no trainer state, which is what makes
them checkable on CPU; `PPOTrainer` only wires them to a model and an optimizer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MASK_EPS = 1e-8


def masked_mean(values: Any, mask: Any) -> Any:
    """Mean over the masked positions only (0 when nothing is masked in)."""
    import torch

    total = (values * mask).sum()
    count = mask.sum()
    return total / torch.clamp(count, min=MASK_EPS)


def gather_logprobs(logits: Any, input_ids: Any) -> Any:
    """log p(token_{t+1} | tokens_<=t), aligned to positions 0..seq_len-2."""
    import torch

    logits = logits[:, :-1, :].float()
    targets = input_ids[:, 1:]
    log_probs = torch.log_softmax(logits, dim=-1)
    return torch.gather(log_probs, dim=-1, index=targets.unsqueeze(-1)).squeeze(-1)


def entropy_from_logits(logits: Any) -> Any:
    import torch

    logits = logits[:, :-1, :].float()
    log_probs = torch.log_softmax(logits, dim=-1)
    return -(log_probs.exp() * log_probs).sum(dim=-1)


def shift_mask(mask: Any) -> Any:
    """Align a token mask with `gather_logprobs` output (drop the first position)."""
    return mask[:, 1:]


def policy_loss(
    logprobs: Any,
    old_logprobs: Any,
    advantages: Any,
    mask: Any,
    clip_ratio: float = 0.2,
) -> tuple[Any, dict[str, float]]:
    """PPO clipped surrogate on action tokens."""
    import torch

    log_ratio = (logprobs - old_logprobs) * mask
    ratio = torch.exp(log_ratio)
    unclipped = -advantages * ratio
    clipped = -advantages * torch.clamp(ratio, 1.0 - clip_ratio, 1.0 + clip_ratio)
    loss = masked_mean(torch.maximum(unclipped, clipped), mask)
    with torch.no_grad():
        clip_fraction = masked_mean((torch.abs(ratio - 1.0) > clip_ratio).float(), mask)
        approx_kl = masked_mean((ratio - 1.0) - log_ratio, mask)  # k3 estimator, non-negative
    return loss, {
        "policy/loss": loss.item(),
        "policy/ratio": masked_mean(ratio, mask).item(),
        "policy/clip_fraction": clip_fraction.item(),
        "policy/approx_kl": approx_kl.item(),
        "policy/advantage_mean": masked_mean(advantages, mask).item(),
    }


def value_loss(
    values: Any,
    returns: Any,
    old_values: Any,
    mask: Any,
    clip: float = 0.2,
) -> tuple[Any, dict[str, float]]:
    """Clipped value regression, as in the standard PPO implementation."""
    import torch

    clipped_values = old_values + torch.clamp(values - old_values, -clip, clip)
    losses = torch.maximum((values - returns) ** 2, (clipped_values - returns) ** 2)
    loss = 0.5 * masked_mean(losses, mask)
    with torch.no_grad():
        explained = 1.0 - masked_mean((returns - values) ** 2, mask) / torch.clamp(
            masked_mean((returns - masked_mean(returns, mask)) ** 2, mask), min=MASK_EPS
        )
    return loss, {"value/loss": loss.item(), "value/explained_variance": explained.item()}


def kl_penalty(logprobs: Any, ref_logprobs: Any, mask: Any, kind: str = "k3") -> Any:
    """Per-token KL(policy || reference) estimate.

    `k3` is the low-variance non-negative estimator exp(d) - d - 1 with
    d = ref - policy; `k1` is the plain difference.
    """
    import torch

    delta = (ref_logprobs - logprobs) * mask
    if kind == "k1":
        return -delta
    return (torch.exp(delta) - delta - 1.0) * mask


@dataclass
class PPOLossConfig:
    clip_ratio: float = 0.2
    value_clip: float = 0.2
    vf_coef: float = 0.5
    entropy_coef: float = 0.0
    kl_coef: float = 0.02
    kl_kind: str = "k3"


def combined_loss(
    logprobs: Any,
    old_logprobs: Any,
    advantages: Any,
    mask: Any,
    config: PPOLossConfig,
    *,
    values: Any = None,
    returns: Any = None,
    old_values: Any = None,
    ref_logprobs: Any = None,
    entropy: Any = None,
) -> tuple[Any, dict[str, float]]:
    """Full PPO objective: surrogate + value + entropy bonus + KL to the reference."""
    loss, metrics = policy_loss(logprobs, old_logprobs, advantages, mask, config.clip_ratio)

    if values is not None and returns is not None:
        critic_loss, critic_metrics = value_loss(
            values, returns, old_values if old_values is not None else values.detach(), mask, config.value_clip
        )
        loss = loss + config.vf_coef * critic_loss
        metrics.update(critic_metrics)

    if entropy is not None and config.entropy_coef:
        bonus = masked_mean(entropy, mask)
        loss = loss - config.entropy_coef * bonus
        metrics["policy/entropy"] = bonus.item()

    if ref_logprobs is not None and config.kl_coef:
        penalty = masked_mean(kl_penalty(logprobs, ref_logprobs, mask, config.kl_kind), mask)
        loss = loss + config.kl_coef * penalty
        metrics["policy/kl_to_reference"] = penalty.item()

    metrics["loss/total"] = loss.item()
    return loss, metrics


class ValueHead:
    """Scalar head over hidden states, used when `critic_mode='shared'`.

    A separate 8B critic doubles optimizer memory for a task whose reward is
    already dense; a head on the actor's own hidden states is the cheaper default,
    with `critic_mode='separate'` available when the value estimate needs to be
    independent of the policy.
    """

    def __init__(self, hidden_size: int, dtype: Any = None) -> None:
        import torch

        self.module = torch.nn.Linear(hidden_size, 1, dtype=dtype)
        torch.nn.init.normal_(self.module.weight, std=1 / (hidden_size + 1) ** 0.5)
        torch.nn.init.zeros_(self.module.bias)

    def __call__(self, hidden_states: Any) -> Any:
        return self.module(hidden_states).squeeze(-1)[:, :-1]  # align with gather_logprobs


__all__ = [
    "PPOLossConfig",
    "ValueHead",
    "combined_loss",
    "entropy_from_logits",
    "gather_logprobs",
    "kl_penalty",
    "masked_mean",
    "policy_loss",
    "shift_mask",
    "value_loss",
]
