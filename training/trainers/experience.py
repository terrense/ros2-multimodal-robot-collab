"""Rollout records -> training experience with turn-level credit on token spans.

This is where the project's central claim becomes code:

    environment step reward  ->  GAE over turns  ->  per-turn advantage
                             ->  the tokens of the action that earned it

`credit.terminal_broadcast=true` switches to the naive alternative (one episode
scalar on every action token) so the two can be compared in the same run
configuration rather than argued about.

Two sequence modes:

    episode  one sequence per episode, one span per turn. Credit flows across the
             whole trajectory in a single forward pass. Requires that the policy
             saw the untruncated history at rollout time.
    turn     one sequence per turn, prompt = the (possibly truncated) history the
             policy actually saw. Required when rollouts truncate context, since
             otherwise the recomputed log-probabilities would be conditioned on
             text the policy never read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import numpy as np

from ..chat_format import build_messages, system_prompt
from ..config import CreditSettings
from ..envs import EpisodeRecord
from ..rewards import RewardConfig, compute_step_rewards
from ..rewards.credit import (
    action_token_mask,
    assign_turn_credit,
    broadcast_to_tokens,
    terminal_broadcast,
    whiten,
)
from ..tokenization import tokenize_episode

SequenceMode = str  # "episode" | "turn"


@dataclass
class TurnExperience:
    """One high-level decision with its reward and its credit."""

    turn_index: int
    reward: float
    value: float = 0.0
    advantage: float = 0.0
    ret: float = 0.0
    reward_breakdown: dict[str, float] = field(default_factory=dict)
    teacher_weight: float = 0.0  # filled in by OPD when the teacher scored this turn
    gate_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "reward": self.reward,
            "value": self.value,
            "advantage": self.advantage,
            "return": self.ret,
            "teacher_weight": self.teacher_weight,
            "gate_reason": self.gate_reason,
        }


@dataclass
class SequenceExperience:
    """One tokenized sequence the trainer will do a forward pass over."""

    input_ids: list[int]
    spans: list[tuple[int, int]]
    turn_indices: list[int]
    advantages: np.ndarray  # per token, zero outside action spans
    returns: np.ndarray  # per token critic target, zero outside action spans
    action_mask: np.ndarray
    #: Filled in by OPD: teacher log-probabilities of the student's sampled tokens
    #: and the per-token distillation weight (0 on turns the gate skipped).
    teacher_logprobs: np.ndarray | None = None
    teacher_weights: np.ndarray | None = None

    @property
    def length(self) -> int:
        return len(self.input_ids)

    @property
    def has_teacher_signal(self) -> bool:
        return self.teacher_logprobs is not None and self.teacher_weights is not None


@dataclass
class EpisodeExperience:
    episode_id: str
    sequences: list[SequenceExperience]
    turns: list[TurnExperience]
    outcome: str
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def total_reward(self) -> float:
        return float(sum(turn.reward for turn in self.turns))

    @property
    def action_tokens(self) -> int:
        return int(sum(sequence.action_mask.sum() for sequence in self.sequences))


@dataclass
class ExperienceStats:
    episodes: int = 0
    turns: int = 0
    action_tokens: int = 0
    dropped_misaligned: int = 0
    dropped_too_long: int = 0
    mean_reward: float = 0.0
    mean_turns: float = 0.0

    def describe(self) -> str:
        return (
            f"{self.episodes} episodes / {self.turns} turns / {self.action_tokens} action tokens; "
            f"mean return {self.mean_reward:.3f}, mean length {self.mean_turns:.1f}; "
            f"dropped {self.dropped_misaligned} misaligned, {self.dropped_too_long} over max_seq_len"
        )


def _turn_prompt(record: EpisodeRecord, index: int, max_history_turns: int | None, system: str) -> list[dict[str, str]]:
    """Rebuild the history the policy saw before turn `index` (turn mode)."""
    steps = [
        {"observation": turn.observation, "action": turn.action, "assistant_text": turn.assistant_text}
        for turn in record.turns
    ]
    messages = build_messages(steps[:index], system=system, max_history_turns=max_history_turns)
    from ..chat_format import observation_content

    messages.append(
        {"role": "user", "content": observation_content(record.turns[index].observation, include_brief=index == 0)}
    )
    return messages


def build_experience(
    record: EpisodeRecord,
    tokenizer: Any,
    *,
    reward_config: RewardConfig,
    credit: CreditSettings | None = None,
    values: Sequence[float] | None = None,
    mode: SequenceMode = "episode",
    max_history_turns: int | None = None,
    max_seq_len: int | None = None,
    system: str | None = None,
) -> EpisodeExperience | None:
    """Tokenize one episode and attach turn credit. Returns None if it is unusable.

    `values` are per-turn critic estimates; without them the advantage falls back
    to return-to-go, which is the `critic_mode="none"` path.
    """
    credit = credit or CreditSettings()
    breakdowns = compute_step_rewards(record.steps_for_reward(), record.outcome, reward_config)
    rewards = [item.total for item in breakdowns]

    credits = assign_turn_credit(rewards, values, gamma=credit.gamma, lam=credit.lam)
    turns = [
        TurnExperience(
            turn_index=record.turns[index].turn_index,
            reward=rewards[index],
            value=credits[index].value,
            advantage=credits[index].advantage,
            ret=credits[index].ret,
            reward_breakdown=breakdowns[index].to_dict(),
        )
        for index in range(len(rewards))
    ]

    total_reward = float(sum(rewards))
    sequences: list[SequenceExperience] = []

    if mode == "episode":
        messages = record.messages or build_messages(
            [{"observation": t.observation, "action": t.action, "assistant_text": t.assistant_text} for t in record.turns],
            system=system,
        )
        try:
            tokens = tokenize_episode(tokenizer, messages)
        except ValueError:
            return None
        if len(tokens.spans) != len(record.turns):
            return None  # a chat template that hides turns would silently misplace credit
        if max_seq_len is not None and len(tokens.input_ids) > max_seq_len:
            return None
        sequences.append(
            _make_sequence(
                tokens.input_ids,
                tokens.spans,
                [turn.turn_index for turn in turns],
                [turn.advantage for turn in turns],
                [turn.ret for turn in turns],
                credit,
                total_reward,
            )
        )
    else:
        system_text = system if system is not None else system_prompt()
        for index, turn in enumerate(turns):
            messages = _turn_prompt(record, index, max_history_turns, system_text)
            messages.append({"role": "assistant", "content": record.turns[index].assistant_text})
            try:
                tokens = tokenize_episode(tokenizer, messages)
            except ValueError:
                return None
            if not tokens.spans:
                return None
            if max_seq_len is not None and len(tokens.input_ids) > max_seq_len:
                return None
            sequences.append(
                _make_sequence(
                    tokens.input_ids,
                    tokens.spans[-1:],
                    [turn.turn_index],
                    [turn.advantage],
                    [turn.ret],
                    credit,
                    total_reward,
                )
            )

    return EpisodeExperience(
        episode_id=record.episode_id,
        sequences=sequences,
        turns=turns,
        outcome=record.outcome,
        summary=dict(record.summary),
    )


def _make_sequence(
    input_ids: list[int],
    spans: Sequence[tuple[int, int]],
    turn_indices: Sequence[int],
    advantages: Sequence[float],
    returns: Sequence[float],
    credit: CreditSettings,
    total_reward: float,
) -> SequenceExperience:
    length = len(input_ids)
    if credit.terminal_broadcast:
        token_advantages = terminal_broadcast(total_reward, spans, length)
    else:
        token_advantages = broadcast_to_tokens(list(advantages), spans, length, mode=credit.broadcast)
    token_returns = broadcast_to_tokens(list(returns), spans, length, mode="uniform")
    return SequenceExperience(
        input_ids=list(input_ids),
        spans=[tuple(span) for span in spans],
        turn_indices=list(turn_indices),
        advantages=token_advantages,
        returns=token_returns,
        action_mask=action_token_mask(spans, length),
    )


def attach_credit(
    experience: EpisodeExperience,
    values: Sequence[float],
    credit: CreditSettings,
) -> EpisodeExperience:
    """Recompute credit with critic values and re-scatter it onto the token spans.

    PPO needs the value estimates before it can form advantages, and the values
    come from a forward pass over the tokenized sequences -- so experience is
    built once without a critic, then refreshed here.
    """
    rewards = [turn.reward for turn in experience.turns]
    credits = assign_turn_credit(rewards, values, gamma=credit.gamma, lam=credit.lam)
    for turn, item in zip(experience.turns, credits):
        turn.value = item.value
        turn.advantage = item.advantage
        turn.ret = item.ret

    by_turn = {turn.turn_index: turn for turn in experience.turns}
    total_reward = float(sum(rewards))
    for sequence in experience.sequences:
        advantages = [by_turn[index].advantage for index in sequence.turn_indices]
        returns = [by_turn[index].ret for index in sequence.turn_indices]
        refreshed = _make_sequence(
            sequence.input_ids, sequence.spans, sequence.turn_indices, advantages, returns, credit, total_reward
        )
        sequence.advantages = refreshed.advantages
        sequence.returns = refreshed.returns
    return experience


def whiten_batch(experiences: Iterable[EpisodeExperience], shift_mean: bool = True) -> None:
    """Normalize advantages across the whole batch, in place.

    Whitening per episode would erase the between-episode signal that a failed
    trajectory is worse than a successful one, so it is done over the batch.
    """
    experiences = list(experiences)
    pooled = np.concatenate(
        [
            sequence.advantages[sequence.action_mask > 0]
            for experience in experiences
            for sequence in experience.sequences
            if sequence.action_mask.sum() > 0
        ]
        or [np.zeros(1)]
    )
    if pooled.size < 2:
        return
    mean = float(pooled.mean()) if shift_mean else 0.0
    std = float(pooled.std()) + 1e-8
    for experience in experiences:
        for sequence in experience.sequences:
            mask = sequence.action_mask > 0
            sequence.advantages[mask] = (sequence.advantages[mask] - mean) / std


def summarize(experiences: Sequence[EpisodeExperience], dropped_misaligned: int = 0, dropped_too_long: int = 0) -> ExperienceStats:
    if not experiences:
        return ExperienceStats(dropped_misaligned=dropped_misaligned, dropped_too_long=dropped_too_long)
    turns = sum(len(experience.turns) for experience in experiences)
    return ExperienceStats(
        episodes=len(experiences),
        turns=turns,
        action_tokens=sum(experience.action_tokens for experience in experiences),
        dropped_misaligned=dropped_misaligned,
        dropped_too_long=dropped_too_long,
        mean_reward=round(sum(experience.total_reward for experience in experiences) / len(experiences), 4),
        mean_turns=round(turns / len(experiences), 2),
    )


__all__ = [
    "EpisodeExperience",
    "ExperienceStats",
    "SequenceExperience",
    "TurnExperience",
    "attach_credit",
    "build_experience",
    "summarize",
    "whiten",
    "whiten_batch",
]
