"""Turn-level credit assignment for long-horizon episodes.

A dense process reward and credit assignment are *not* the same thing. A process
reward only makes the signal arrive more often; credit assignment decides which
decision the signal is attributed to. This module is the second half:

    environment step reward  ->  GAE / return-to-go  ->  per-turn advantage
                             ->  token spans of the action that produced it

`terminal_broadcast` implements the naive alternative (one final scalar spread
uniformly over every generated token) and exists so the ablation can show the
difference rather than assert it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Sequence

import numpy as np

BroadcastMode = Literal["uniform", "last_token", "first_token"]


@dataclass
class TurnCredit:
    """Credit attached to one high-level action."""

    index: int
    reward: float
    value: float
    advantage: float
    ret: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def discounted_returns(rewards: Sequence[float], gamma: float = 0.99) -> np.ndarray:
    """Return-to-go: R_t = r_t + gamma * R_{t+1}."""
    out = np.zeros(len(rewards), dtype=np.float64)
    running = 0.0
    for index in range(len(rewards) - 1, -1, -1):
        running = float(rewards[index]) + gamma * running
        out[index] = running
    return out


def compute_gae(
    rewards: Sequence[float],
    values: Sequence[float],
    gamma: float = 0.99,
    lam: float = 0.95,
    last_value: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Generalized advantage estimation over turns.

    Returns `(advantages, returns)` where `returns = advantages + values`, the
    standard PPO critic target. `last_value` is the bootstrap for a truncated
    episode; it stays 0.0 for terminated episodes.
    """
    if len(rewards) != len(values):
        raise ValueError(f"rewards ({len(rewards)}) and values ({len(values)}) must align")
    horizon = len(rewards)
    advantages = np.zeros(horizon, dtype=np.float64)
    running = 0.0
    next_value = float(last_value)
    for index in range(horizon - 1, -1, -1):
        delta = float(rewards[index]) + gamma * next_value - float(values[index])
        running = delta + gamma * lam * running
        advantages[index] = running
        next_value = float(values[index])
    returns = advantages + np.asarray(values, dtype=np.float64)
    return advantages, returns


def whiten(values: np.ndarray, shift_mean: bool = True, eps: float = 1e-8) -> np.ndarray:
    """Advantage normalization. Single-element batches are passed through."""
    array = np.asarray(values, dtype=np.float64)
    if array.size < 2:
        return array - array.mean() if shift_mean else array
    normalized = (array - array.mean()) / (array.std() + eps)
    return normalized if shift_mean else normalized + array.mean()


def assign_turn_credit(
    rewards: Sequence[float],
    values: Sequence[float] | None = None,
    gamma: float = 0.99,
    lam: float = 0.95,
    last_value: float = 0.0,
) -> list[TurnCredit]:
    """Per-turn advantages. With no critic, falls back to return-to-go as advantage."""
    if values is None:
        returns = discounted_returns(rewards, gamma)
        return [
            TurnCredit(index=i, reward=float(rewards[i]), value=0.0, advantage=float(returns[i]), ret=float(returns[i]))
            for i in range(len(rewards))
        ]
    advantages, returns = compute_gae(rewards, values, gamma, lam, last_value)
    return [
        TurnCredit(
            index=i,
            reward=float(rewards[i]),
            value=float(values[i]),
            advantage=float(advantages[i]),
            ret=float(returns[i]),
        )
        for i in range(len(rewards))
    ]


def broadcast_to_tokens(
    credits: Sequence[TurnCredit] | Sequence[float],
    spans: Sequence[tuple[int, int]],
    seq_len: int,
    mode: BroadcastMode = "uniform",
) -> np.ndarray:
    """Scatter per-turn advantages onto the token positions that produced them.

    `spans[i]` is the `[start, end)` token range of turn `i`'s assistant message
    inside one tokenized episode. Positions outside every span stay 0.0 and are
    masked out of the policy-gradient loss.

    mode:
      uniform     every action token of the turn carries the turn advantage
      last_token  only the final token (cheap, higher variance)
      first_token only the first token (probes the commitment point of the decision)
    """
    if len(credits) != len(spans):
        raise ValueError(f"credits ({len(credits)}) and spans ({len(spans)}) must align")
    out = np.zeros(seq_len, dtype=np.float64)
    for credit, (start, end) in zip(credits, spans):
        advantage = credit.advantage if isinstance(credit, TurnCredit) else float(credit)
        start = max(0, int(start))
        end = min(seq_len, int(end))
        if end <= start:
            continue
        if mode == "uniform":
            out[start:end] = advantage
        elif mode == "last_token":
            out[end - 1] = advantage
        elif mode == "first_token":
            out[start] = advantage
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown broadcast mode {mode!r}")
    return out


def terminal_broadcast(total_reward: float, spans: Sequence[tuple[int, int]], seq_len: int) -> np.ndarray:
    """The naive baseline: one episode scalar spread over every generated token.

    Kept so the credit-assignment ablation compares against a real implementation
    instead of a described one.
    """
    out = np.zeros(seq_len, dtype=np.float64)
    for start, end in spans:
        start = max(0, int(start))
        end = min(seq_len, int(end))
        if end > start:
            out[start:end] = float(total_reward)
    return out


def action_token_mask(spans: Sequence[tuple[int, int]], seq_len: int) -> np.ndarray:
    """1.0 on tokens the policy generated, 0.0 on observation/system tokens."""
    mask = np.zeros(seq_len, dtype=np.float64)
    for start, end in spans:
        start = max(0, int(start))
        end = min(seq_len, int(end))
        if end > start:
            mask[start:end] = 1.0
    return mask


__all__ = [
    "BroadcastMode",
    "TurnCredit",
    "action_token_mask",
    "assign_turn_credit",
    "broadcast_to_tokens",
    "compute_gae",
    "discounted_returns",
    "terminal_broadcast",
    "whiten",
]
