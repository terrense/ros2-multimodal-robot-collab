"""Episode budgets: turns, policy-reported tokens, wall time.

Budgets are checked before every decision and reported in the episode result so
a truncated run is never mistaken for a completed one.
"""

from __future__ import annotations

from dataclasses import dataclass

TURNS = "budget_turns"
TOKENS = "budget_tokens"
WALL = "budget_wall"


@dataclass(frozen=True)
class Budgets:
    max_turns: int | None = None
    # Named in characters on purpose: a stub policy cannot count real tokens,
    # and pretending otherwise would make the number meaningless.
    max_tokens_chars: int | None = None
    max_wall_s: float | None = None

    def exceeded(self, *, turns: int, tokens: int, elapsed_s: float) -> str | None:
        if self.max_turns is not None and turns >= self.max_turns:
            return TURNS
        if self.max_tokens_chars is not None and tokens >= self.max_tokens_chars:
            return TOKENS
        if self.max_wall_s is not None and elapsed_s >= self.max_wall_s:
            return WALL
        return None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "max_turns": self.max_turns,
            "max_tokens_chars": self.max_tokens_chars,
            "max_wall_s": self.max_wall_s,
        }
