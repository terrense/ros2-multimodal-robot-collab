"""On-policy distillation loss and the OPD -> PPO mix.

The student rolls out; the teacher scores the tokens the student sampled. Two
ways to use that signal, both implemented because the experiment compares them:

    direct_kl   per-token reverse KL on the student's own samples, weighted per
                turn. This is pure OPD: the objective is "be less wrong on the
                states you actually reach", with no environment reward at all.
    reward      the same teacher/student difference enters as a dense per-turn
                reward and flows through GAE like any other reward, so it can be
                mixed with the environment reward inside one PPO update.

Reverse KL uses the k3 estimator, exp(d) - d - 1 with d = teacher - student. It
is non-negative, low variance, and differentiable through the student's
log-probabilities, so it needs no separate policy-gradient term.

Both paths are weighted per turn: turns where the student was confident and the
teacher disagreed carry more weight than turns both agree on (see
`training.policies.teacher.distillation_weight`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .ppo import MASK_EPS, masked_mean


def reverse_kl_k3(student_logprobs: Any, teacher_logprobs: Any) -> Any:
    """Per-token KL(student || teacher) estimate; zero when the two agree."""
    import torch

    delta = teacher_logprobs - student_logprobs
    return torch.exp(delta) - delta - 1.0


def opd_loss(
    student_logprobs: Any,
    teacher_logprobs: Any,
    mask: Any,
    weights: Any = None,
) -> tuple[Any, dict[str, float]]:
    """Weighted reverse-KL distillation loss on the student's sampled tokens.

    Normalized by the number of *scored* tokens rather than by the sum of the
    weights: a weighted average would make the turn weights purely relative, so
    doubling every weight would change nothing and "more weight where the student
    is uncertain" would have no effect across episodes. Dividing by the scored
    token count keeps the weights meaningful while leaving turns the gate skipped
    out of both the numerator and the denominator.
    """
    import torch

    per_token = reverse_kl_k3(student_logprobs, teacher_logprobs)
    if weights is None:
        weights = torch.ones_like(mask)
    scored = mask * (weights > 0).to(mask.dtype)
    numerator = (per_token * weights * mask).sum()
    loss = numerator / torch.clamp(scored.sum(), min=MASK_EPS)
    with torch.no_grad():
        disagreement = masked_mean(student_logprobs - teacher_logprobs, mask)
    return loss, {
        "opd/loss": loss.item(),
        "opd/reverse_kl": masked_mean(per_token, scored).item(),
        "opd/disagreement": disagreement.item(),
        "opd/weight_mean": masked_mean(weights, scored).item(),
        "opd/scored_fraction": (scored.sum() / torch.clamp(mask.sum(), min=MASK_EPS)).item(),
    }


def _field(item: Any, name: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def teacher_turn_rewards(
    distillations: Sequence[Any],
    turn_count: int,
    scale: float = 1.0,
    positions: dict[int, int] | None = None,
) -> list[float]:
    """Dense per-turn reward from teacher scoring, for the `reward` OPD path.

    A turn the teacher liked less than the student did (positive disagreement)
    gets a negative reward, so the same GAE machinery that handles environment
    reward also handles teacher guidance. Unscored turns contribute nothing.

    `positions` maps a turn index to its slot in the episode; without it the turn
    index is assumed to be 1-based and contiguous.
    """
    rewards = [0.0] * turn_count
    for item in distillations:
        turn_index = int(_field(item, "turn_index", 0))
        index = positions.get(turn_index, -1) if positions is not None else turn_index - 1
        if not 0 <= index < turn_count:
            continue
        disagreement = float(_field(item, "disagreement", 0.0))
        weight = float(_field(item, "weight", 1.0))
        rewards[index] = -scale * weight * disagreement
    return rewards


@dataclass
class MixedLossConfig:
    """Weights for one training step of the OPD -> PPO handover."""

    opd_weight: float = 1.0
    env_weight: float = 0.0
    opd_loss: str = "direct_kl"  # direct_kl | reward

    @property
    def uses_direct_kl(self) -> bool:
        return self.opd_loss == "direct_kl"


def mixed_loss(
    ppo_term: Any,
    opd_term: Any,
    config: MixedLossConfig,
) -> tuple[Any, dict[str, float]]:
    """`env_weight * PPO + opd_weight * OPD`, with both weights logged.

    Early in training `opd_weight` dominates and the student learns what a valid
    recovery looks like. Late in training `env_weight` dominates and the
    simulator, not the teacher's opinion, decides what actually works.
    """
    total = config.env_weight * ppo_term + config.opd_weight * opd_term
    return total, {
        "mix/opd_weight": config.opd_weight,
        "mix/env_weight": config.env_weight,
        "mix/loss": float(total.item()) if hasattr(total, "item") else float(total),
    }


def build_turn_weight_tensor(
    spans: Sequence[tuple[int, int]],
    weights: Sequence[float],
    seq_len: int,
    device: Any = None,
) -> Any:
    """Scatter per-turn distillation weights onto their action tokens.

    Turns the gate skipped keep weight 0, which is what confines the teacher's
    influence to the turns it was actually asked about.
    """
    import torch

    tensor = torch.zeros(seq_len, dtype=torch.float32, device=device)
    for (start, end), weight in zip(spans, weights, strict=False):
        start = max(0, int(start))
        end = min(seq_len, int(end))
        if end > start:
            tensor[start:end] = float(weight)
    return tensor


__all__ = [
    "MixedLossConfig",
    "build_turn_weight_tensor",
    "mixed_loss",
    "opd_loss",
    "reverse_kl_k3",
    "teacher_turn_rewards",
]
