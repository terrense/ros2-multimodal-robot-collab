"""Policy backends for the closed loop.

Each backend answers the same question: given one model view, what is the next
single action? Only the source of the answer differs.

- ScriptedPolicy: a fixed list (the baseline).
- StubModelPolicy: a deterministic stand-in that also counts characters, so the
  token budget can be exercised without a network or a tokenizer.
- HumanPolicy: a callback, used by a debugging driver or a test.
- PlanMacroPolicy: a validated mission plan replayed one step per decision; a
  failed step ends the macro, exactly like the executor's `break`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .policy import Decide, Decision, Finish


class ScriptedPolicy:
    name = "scripted"

    def __init__(self, decisions: Iterable[Decision]) -> None:
        self._decisions = list(decisions)
        self._index = 0
        self.receipts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._index = 0
        self.receipts = []

    def decide(self, view: dict[str, Any]) -> Decision:
        del view
        if self._index < len(self._decisions):
            decision = self._decisions[self._index]
            self._index += 1
            return decision
        return Finish(claimed_success=False, reason="script exhausted")

    def consume(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)

    def tokens_used(self) -> int:
        return 0


class StubModelPolicy:
    """Deterministic stand-in for a model backend."""

    name = "stub-model"

    def __init__(self, decisions: Iterable[Decision]) -> None:
        self._inner = ScriptedPolicy(decisions)
        self._chars = 0

    def reset(self) -> None:
        self._inner.reset()
        self._chars = 0

    def decide(self, view: dict[str, Any]) -> Decision:
        self._chars += len(json.dumps(view, ensure_ascii=False, default=str))
        return self._inner.decide(view)

    def consume(self, receipt: dict[str, Any]) -> None:
        self._chars += len(json.dumps(receipt, ensure_ascii=False, default=str))
        self._inner.consume(receipt)

    def tokens_used(self) -> int:
        return self._chars


class HumanPolicy:
    name = "human"

    def __init__(self, responder: Callable[[dict[str, Any]], Decision]) -> None:
        self._responder = responder
        self.receipts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.receipts = []

    def decide(self, view: dict[str, Any]) -> Decision:
        return self._responder(view)

    def consume(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)

    def tokens_used(self) -> int:
        return 0


class PlanMacroPolicy:
    """Replay a validated mission plan, one step per decision."""

    name = "plan-macro"

    def __init__(self, steps: Sequence[tuple[str, dict[str, Any]]]) -> None:
        self._steps = list(steps)
        self._index = 0
        self._failed = False
        self.receipts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._index = 0
        self._failed = False
        self.receipts = []

    def decide(self, view: dict[str, Any]) -> Decision:
        del view
        if self._failed:
            return Finish(claimed_success=False, reason="a plan step failed")
        if self._index >= len(self._steps):
            return Finish(claimed_success=True, reason="plan complete")
        capability, arguments = self._steps[self._index]
        self._index += 1
        return Decide(capability=capability, arguments=dict(arguments))

    def consume(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)
        if receipt.get("execution_status") in {"failed", "rejected", "unknown"}:
            self._failed = True

    def tokens_used(self) -> int:
        return 0
