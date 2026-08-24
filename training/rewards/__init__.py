"""Reward shaping and credit assignment.

`step_reward` decides *how much* signal each turn carries.
`credit` decides *which turn* that signal is attributed to.

They are separate on purpose: a dense process reward is not credit assignment.
"""

from .credit import (
    TurnCredit,
    assign_turn_credit,
    broadcast_to_tokens,
    compute_gae,
    discounted_returns,
    terminal_broadcast,
    whiten,
)
from .step_reward import PRESETS, RewardConfig, StepReward, compute_step_rewards, episode_reward

__all__ = [
    "PRESETS",
    "RewardConfig",
    "StepReward",
    "TurnCredit",
    "assign_turn_credit",
    "broadcast_to_tokens",
    "compute_gae",
    "compute_step_rewards",
    "discounted_returns",
    "episode_reward",
    "terminal_broadcast",
    "whiten",
]
