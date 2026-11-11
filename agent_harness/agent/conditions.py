"""The named evaluation conditions (Phase 6).

The master plan asks for a specific set of baselines, each isolating one thing:

    existing rules, expert, single-shot LLM planner, closed-loop without
    receipts, with receipts, with snapshot freshness, with recovery.

`rules` and `expert` live in `training/policies/`. `planner_once` is
`PlanMacroPolicy`. The four loop conditions are here, and they are *cumulative
and declarative*: each one switches on exactly one more thing, so the
difference between two rows is the value of that thing and nothing else.

    loop_no_receipts      the policy is not told what happened
    loop_receipts         it is
    loop_receipts_fresh   and a stale snapshot is refused
    loop_receipts_recovery and it is told which operations it may choose from

"Which operations it may choose from" is not a hardcoded prompt. It is the
capability registry's model-visible list, derived from the operations that are
actually registered -- the same rule Phase 4 set for capability discovery. A
policy that cannot see the registry has to guess what it is allowed to call,
which is a different question from "can it recover".

The one thing this module is careful about: `loop_no_receipts` has to hide
*both* the runtime receipt and the environment's own report of the last result.
On L1 the observation carries `last_result`, so hiding only the injected
receipt would leave the outcome visible and the ablation would measure
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopCondition:
    """One named closed-loop configuration."""

    name: str
    #: Does the policy learn what the last command did?
    receipts_visible: bool = True
    #: Is a snapshot that has gone stale refused?
    freshness_gate: bool = True
    #: Is the policy shown the operations it may choose from?
    recovery_visible: bool = False
    #: What this row is meant to isolate.
    isolates: str = ""

    def describe(self) -> str:
        parts = []
        parts.append("receipts" if self.receipts_visible else "no receipts")
        parts.append("fresh" if self.freshness_gate else "no freshness gate")
        parts.append("recovery" if self.recovery_visible else "no recovery")
        return f"{self.name}: " + ", ".join(parts)


#: The four loop conditions, cumulative.
LOOP_NO_RECEIPTS = LoopCondition(
    name="loop_no_receipts",
    receipts_visible=False,
    freshness_gate=False,
    recovery_visible=False,
    isolates="the value of observing the result",
)
LOOP_RECEIPTS = LoopCondition(
    name="loop_receipts",
    receipts_visible=True,
    freshness_gate=False,
    recovery_visible=False,
    isolates="the receipt",
)
LOOP_RECEIPTS_FRESH = LoopCondition(
    name="loop_receipts_fresh",
    receipts_visible=True,
    freshness_gate=True,
    recovery_visible=False,
    isolates="the snapshot freshness gate",
)
LOOP_RECEIPTS_RECOVERY = LoopCondition(
    name="loop_receipts_recovery",
    receipts_visible=True,
    freshness_gate=True,
    recovery_visible=True,
    isolates="explicit recovery decisions",
)

#: Every loop condition, in the order the report should list them.
LOOP_CONDITIONS: tuple[LoopCondition, ...] = (
    LOOP_NO_RECEIPTS,
    LOOP_RECEIPTS,
    LOOP_RECEIPTS_FRESH,
    LOOP_RECEIPTS_RECOVERY,
)

#: What the closed loop does when no condition is named. The current behaviour:
#: receipts and the freshness gate on, recovery not surfaced.
DEFAULT_CONDITION = LOOP_RECEIPTS_FRESH


def by_name(name: str) -> LoopCondition:
    for condition in LOOP_CONDITIONS:
        if condition.name == name:
            return condition
    known = ", ".join(condition.name for condition in LOOP_CONDITIONS)
    raise KeyError(f"unknown condition {name!r}; known: {known}")


__all__ = [
    "DEFAULT_CONDITION",
    "LOOP_CONDITIONS",
    "LOOP_NO_RECEIPTS",
    "LOOP_RECEIPTS",
    "LOOP_RECEIPTS_FRESH",
    "LOOP_RECEIPTS_RECOVERY",
    "LoopCondition",
    "by_name",
]
