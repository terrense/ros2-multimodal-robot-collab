"""Composite step-level reward for the delivery agent.

Five sources, all grounded in the environment rather than in an LLM judge:

    terminal      outcome of the episode (success / correct abort / cancel / wrong stop / step limit)
    progress      sub-goal ladder delta from the phase estimator (`PHASE_PROGRESS`)
    grounding     schema legality of the emitted skill call
    safety        motion without authorization, handover without confirmation, motion after cancel
    cost          per-step cost + no-progress and repeated-call penalties

`RewardConfig` is the ablation surface: `PRESETS["terminal_only"]` reproduces
outcome-only PPO, `PRESETS["progress"]` is the full composite reward, and the
`progress_no_*` presets drop exactly one component so an ablation table can say
which component fixed which failure mode.

Reward magnitudes are deliberately small next to the terminal term: dense shaping
is there to make early mistakes learnable, not to outvote the real task outcome.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Mapping

#: Terminal values keyed by `agent_env.protocol.Outcome`.
DEFAULT_TERMINAL_VALUES: dict[str, float] = {
    "success": 1.0,
    "correct_abort": 0.6,  # reporting an unrecoverable fault is correct behaviour, not failure
    "cancelled_ok": 0.6,
    "wrong_termination": -1.0,  # claiming success without delivering, or giving up too early
    "step_limit": -0.5,
    "running": 0.0,
}


@dataclass(frozen=True)
class RewardConfig:
    name: str = "progress"
    w_terminal: float = 1.0
    terminal_values: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_TERMINAL_VALUES))
    w_progress: float = 1.0
    regression_scale: float = 0.5  # weight on negative progress deltas (e.g. a mid-transit drop)
    w_grounding: float = 0.30
    w_safety: float = 1.00
    w_step_cost: float = 0.01
    w_no_progress: float = 0.05
    no_progress_threshold: int = 2
    w_repeat: float = 0.05
    repeat_window: int = 3
    clip: tuple[float, float] = (-2.0, 2.0)

    def with_overrides(self, **overrides: Any) -> "RewardConfig":
        return replace(self, **overrides)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["terminal_values"] = dict(self.terminal_values)
        data["clip"] = list(self.clip)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RewardConfig":
        payload = dict(data)
        preset = payload.pop("preset", None)
        base = PRESETS[preset] if preset else cls()
        if "clip" in payload:
            payload["clip"] = tuple(payload["clip"])  # yaml gives a list
        if "terminal_values" in payload:
            payload["terminal_values"] = dict(payload["terminal_values"])
        return replace(base, **payload)

    @classmethod
    def preset(cls, name: str) -> "RewardConfig":
        try:
            return PRESETS[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise KeyError(f"unknown reward preset {name!r}; known: {sorted(PRESETS)}") from exc


_FULL = RewardConfig(name="progress")

#: Ablation grid. `terminal_only` is the Outcome-PPO baseline the project argues against.
PRESETS: dict[str, RewardConfig] = {
    "terminal_only": RewardConfig(
        name="terminal_only",
        w_progress=0.0,
        w_grounding=0.0,
        w_safety=0.0,
        w_step_cost=0.0,
        w_no_progress=0.0,
        w_repeat=0.0,
    ),
    "terminal_safety": RewardConfig(
        name="terminal_safety",
        w_progress=0.0,
        w_step_cost=0.0,
        w_no_progress=0.0,
        w_repeat=0.0,
    ),
    "progress": _FULL,
    "progress_no_grounding": _FULL.with_overrides(name="progress_no_grounding", w_grounding=0.0),
    "progress_no_safety": _FULL.with_overrides(name="progress_no_safety", w_safety=0.0),
    "progress_no_cost": _FULL.with_overrides(
        name="progress_no_cost", w_step_cost=0.0, w_no_progress=0.0, w_repeat=0.0
    ),
    "progress_no_terminal": _FULL.with_overrides(name="progress_no_terminal", w_terminal=0.0),
}


@dataclass
class StepReward:
    """One turn's reward with its per-component breakdown (for ablation tables)."""

    total: float
    terminal: float = 0.0
    progress: float = 0.0
    grounding: float = 0.0
    safety: float = 0.0
    cost: float = 0.0
    no_progress: float = 0.0
    repeat: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def _canonical_action(action: Mapping[str, Any] | None) -> str:
    if not action:
        return ""
    args = action.get("args") or {}
    # `reason` is free text and must not make two identical calls look different.
    return json.dumps(
        {"skill": action.get("skill"), "action": action.get("action"), "args": args},
        sort_keys=True,
        ensure_ascii=False,
    )


def compute_step_rewards(
    steps: Iterable[Mapping[str, Any]],
    outcome: str,
    config: RewardConfig | None = None,
) -> list[StepReward]:
    """Per-turn rewards for one episode.

    Each step needs `reward_components` (as emitted by `L1DeliveryEnv.step`) and
    `action`; `info.no_progress_count` is used when present, otherwise the
    no-progress penalty is derived from repeated identical calls only.
    """
    config = config or PRESETS["progress"]
    steps = list(steps)
    rewards: list[StepReward] = []
    seen: list[str] = []

    for index, step in enumerate(steps):
        components = dict(step.get("reward_components") or {})
        info = dict(step.get("info") or {})
        action = step.get("action")

        delta = float(components.get("progress_delta", 0.0))
        scale = 1.0 if delta >= 0 else config.regression_scale
        progress = config.w_progress * delta * scale
        grounding = -config.w_grounding * float(components.get("grounding_violation", 0.0))
        safety = -config.w_safety * float(components.get("safety_violation", 0.0))
        cost = -config.w_step_cost * float(components.get("step_cost", 1.0))

        stuck = int(info.get("no_progress_count", 0))
        no_progress = -config.w_no_progress if stuck >= config.no_progress_threshold else 0.0

        signature = _canonical_action(action)
        window = seen[-config.repeat_window :] if config.repeat_window > 0 else []
        repeat = -config.w_repeat if signature and signature in window else 0.0
        seen.append(signature)

        terminal = 0.0
        if index == len(steps) - 1:
            terminal = config.w_terminal * float(config.terminal_values.get(outcome, 0.0))

        total = progress + grounding + safety + cost + no_progress + repeat + terminal
        low, high = config.clip
        total = max(low, min(high, total))
        rewards.append(
            StepReward(
                total=round(total, 6),
                terminal=round(terminal, 6),
                progress=round(progress, 6),
                grounding=round(grounding, 6),
                safety=round(safety, 6),
                cost=round(cost, 6),
                no_progress=round(no_progress, 6),
                repeat=round(repeat, 6),
            )
        )
    return rewards


def episode_reward(
    steps: Iterable[Mapping[str, Any]],
    outcome: str,
    config: RewardConfig | None = None,
) -> float:
    """Undiscounted episode return; the scalar an outcome-only baseline would see."""
    return round(sum(reward.total for reward in compute_step_rewards(steps, outcome, config)), 6)


__all__ = [
    "DEFAULT_TERMINAL_VALUES",
    "PRESETS",
    "RewardConfig",
    "StepReward",
    "compute_step_rewards",
    "episode_reward",
]
