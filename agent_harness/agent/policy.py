"""Policy-facing types for the closed loop (Phase 5).

A policy sees one model view and returns exactly one decision. Two decisions
exist: act on one capability, or finish. Nothing here knows what a ROS action
is; the loop translates decisions through the runtime.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, Union

from agent_harness.contracts import TaskOutcome


@dataclass(frozen=True)
class Decide:
    """Act on exactly one capability this turn."""

    capability: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class Finish:
    """Stop the episode. ``claimed_success`` is a claim, never the verdict."""

    claimed_success: bool
    reason: str = ""


Decision = Union[Decide, Finish]


class Policy(Protocol):
    name: str

    def reset(self) -> None: ...

    def decide(self, view: dict[str, Any]) -> Decision: ...

    def consume(self, receipt: dict[str, Any]) -> None: ...

    def tokens_used(self) -> int: ...


@dataclass
class EpisodeResult:
    """What the loop produced. ``outcome.success`` is never a policy claim."""

    outcome: TaskOutcome
    turns: int
    receipts: list[dict[str, Any]] = field(default_factory=list)
    finish: Finish | None = None
    failure: str | None = None
    claimed_success: bool | None = None
    budgets: dict[str, Any] = field(default_factory=dict)


def wall_clock() -> float:
    return time.time()
