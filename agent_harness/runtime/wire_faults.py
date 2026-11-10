"""Fault injection at the transport layer, and the invariants it must not break (O3.1).

The eight transport faults the runtime handles are injected in-process: a fake
backend returns a timeout, a dropped result, a rejected goal. That proves the
*state machine* is right. It does not prove the real transport is, because the
real transport can lose a message anywhere — including after the robot moved
and before anyone heard about it.

So this module has two halves.

`NetemRule` and `build_commands` turn a fault description into `tc netem`
commands. Nothing here runs them; the CLI does that, and restores the interface
in a `finally`.

`check_ledger` is the part that matters. A wire fault is survivable only if
three things stay true afterwards, and each one has a specific failure it
prevents:

    no_duplicate_effect     a command that timed out must never be dispatched
                            a second time; the robot would move twice
    uncertain_stays_unknown a timeout must be recorded as `unknown`, never as
                            `failed`; filing it as a failure is what invites
                            the retry
    blocked_until_ack       once a command is unresolved, no further
                            side-effecting command may be dispatched until a
                            human acknowledged it

Those checks read the ledger, so they work on a real run and on a recorded one.
They are pure functions and are tested without ROS 2 or root.
"""

from __future__ import annotations

import shlex
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

#: CycloneDDS starts here by default and walks upward; ROS 2 domain ids offset it.
DDS_BASE_PORT = 7400
DDS_PORT_SPAN = 250


@dataclass(frozen=True)
class NetemRule:
    """One transport fault, expressed the way `tc` expresses it."""

    loss_percent: float = 0.0
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    duplicate_percent: float = 0.0
    reorder_percent: float = 0.0
    #: Correlation between successive losses, 0-100. Real links lose in bursts.
    correlation_percent: float = 0.0

    @property
    def empty(self) -> bool:
        return not (
            self.loss_percent
            or self.delay_ms
            or self.duplicate_percent
            or self.reorder_percent
        )

    def netem_arguments(self) -> list[str]:
        """The `netem` parameters, in the order tc expects them."""
        parts: list[str] = []
        if self.loss_percent:
            parts.append(f"loss {self.loss_percent:g}%")
            if self.correlation_percent:
                parts.append(f" {self.correlation_percent:g}%")
        if self.delay_ms:
            parts.append(f"delay {self.delay_ms:g}ms")
            if self.jitter_ms:
                parts.append(f" {self.jitter_ms:g}ms")
        if self.duplicate_percent:
            parts.append(f"duplicate {self.duplicate_percent:g}%")
        if self.reorder_percent:
            parts.append(f"reorder {self.reorder_percent:g}%")
        return " ".join(parts).split()

    def describe(self) -> str:
        if self.empty:
            return "no fault"
        return " ".join(self.netem_arguments())


@dataclass(frozen=True)
class FaultScenario:
    """A named fault, and what a run under it is supposed to survive."""

    name: str
    rule: NetemRule
    expectation: str
    #: The command that is being made unreliable, for the report.
    capability: str = "navigation.navigate_to_station"
    #: Arguments that command needs. A command rejected for missing arguments
    #: never reaches the transport, so the fault would test nothing.
    arguments: dict[str, Any] = field(
        default_factory=lambda: {"station_id": "station_a", "reason": "wire fault test"}
    )


SCENARIOS: dict[str, FaultScenario] = {
    "dropped_result": FaultScenario(
        name="dropped_result",
        rule=NetemRule(loss_percent=30.0, correlation_percent=25.0),
        expectation=(
            "the goal may complete while the result is lost; the ledger must show "
            "`unknown`, never `failed`, and nothing may replay it"
        ),
    ),
    "slow_ack": FaultScenario(
        name="slow_ack",
        rule=NetemRule(delay_ms=400.0, jitter_ms=120.0),
        expectation="a delayed acknowledgement is a slow success, not a timeout",
    ),
    "duplicated_packets": FaultScenario(
        name="duplicated_packets",
        rule=NetemRule(duplicate_percent=20.0),
        expectation="a duplicated frame must not become a duplicated command",
    ),
    "bursty_loss": FaultScenario(
        name="bursty_loss",
        rule=NetemRule(loss_percent=15.0, correlation_percent=75.0),
        expectation=(
            "loss in bursts is the realistic case; the fail-closed path must hold "
            "and stay blocked until an operator acknowledges"
        ),
    ),
    "severe": FaultScenario(
        name="severe",
        rule=NetemRule(loss_percent=60.0, delay_ms=200.0, correlation_percent=50.0),
        expectation="most commands end unknown; the ledger must be mostly `unknown`, not mostly `failed`",
    ),
}


def build_commands(
    rule: NetemRule,
    *,
    interface: str = "lo",
    port_range: tuple[int, int] | None = None,
    binary: str = "tc",
) -> dict[str, list[list[str]]]:
    """The tc commands that apply and clear a fault.

    With `port_range`, the fault covers only that range — usually the DDS ports
    for one ROS domain. Without it, it covers the whole interface, which on
    loopback means every ROS 2 node on the machine. That is heavier than it
    needs to be and it is the honest default to state: the ports CycloneDDS
    picks are not fixed, so a port filter can miss traffic silently, and a
    filter that silently misses traffic produces a green result that proves
    nothing.
    """
    apply: list[list[str]] = [
        [binary, "qdisc", "del", "dev", interface, "root"],
        [binary, "qdisc", "add", "dev", interface, "root", "handle", "1:", "prio"],
        [
            binary, "qdisc", "add", "dev", interface, "parent", "1:3",
            "handle", "30:", "netem", *rule.netem_arguments(),
        ],
    ]
    if port_range is not None:
        low, high = port_range
        apply.append(
            [
                binary, "filter", "add", "dev", interface, "protocol", "ip",
                "parent", "1:0", "prio", "3", "u32",
                "match", "ip", "dport", str(low), "0xffff",
                "flowid", "1:3",
            ]
        )
        # A range needs a second match term; tc u32 has no native range syntax
        # that is worth the fragility here, so the caller passes one port.
        if high != low:
            raise ValueError(
                "tc u32 cannot express a port range portably; pass the single "
                "port to target, or omit port_range to cover the whole interface"
            )
    else:
        apply.append(
            [
                binary, "filter", "add", "dev", interface, "protocol", "ip",
                "parent", "1:0", "prio", "3", "u32", "match", "u32", "0", "0",
                "flowid", "1:3",
            ]
        )
    clear: list[list[str]] = [[binary, "qdisc", "del", "dev", interface, "root"]]
    return {"apply": apply, "clear": clear}


def render(commands: Sequence[Sequence[str]]) -> str:
    """The commands as a shell block, for the report and for the log."""
    return "\n".join(" ".join(shlex.quote(part) for part in command) for command in commands)


# ---------------------------------------------------------------- the checks


@dataclass
class LedgerView:
    """What the ledger shows for one experiment."""

    #: command_id -> final status string.
    statuses: dict[str, str] = field(default_factory=dict)
    #: command_id -> how many `started` events it has.
    starts: Counter = field(default_factory=Counter)
    #: Ordered (command_id, kind) pairs, so ordering rules can be checked.
    events: list[tuple[str, str]] = field(default_factory=list)
    #: command_ids an operator acknowledged.
    acknowledged: set[str] = field(default_factory=set)

    @classmethod
    def from_ledger(cls, ledger: Any) -> "LedgerView":
        """Read a live `ExecutionLedger` after the run.

        This is a post-hoc audit, not a live monitor: the checks read final
        statuses and then walk the event order. That is enough to answer "did
        the system double-apply a command", which is the question the whole
        ledger exists for.
        """
        view = cls()
        for row in ledger.all_records():
            view.statuses[row.command_id] = str(getattr(row.status, "value", row.status))
            if getattr(row, "reconciled_by", None):
                view.acknowledged.add(row.command_id)
        for command_id, kind in ledger.all_events():
            view.events.append((command_id, kind))
            if kind == "started":
                view.starts[command_id] += 1
        return view


@dataclass
class WireFaultVerdict:
    """The three invariants, plus what failed."""

    no_duplicate_effect: bool
    uncertain_stays_unknown: bool
    blocked_until_ack: bool
    failures: list[str] = field(default_factory=list)

    @property
    def survived(self) -> bool:
        return self.no_duplicate_effect and self.uncertain_stays_unknown and self.blocked_until_ack

    def report(self) -> str:
        mark = "PASS" if self.survived else "FAIL"
        lines = [f"wire fault: {mark}"]
        lines.append(f"  no duplicate effect:      {'ok' if self.no_duplicate_effect else 'violated'}")
        lines.append(f"  uncertain stays unknown:  {'ok' if self.uncertain_stays_unknown else 'violated'}")
        lines.append(f"  blocked until acknowledged: {'ok' if self.blocked_until_ack else 'violated'}")
        for failure in self.failures:
            lines.append(f"  - {failure}")
        return "\n".join(lines)


def check_ledger(
    view: LedgerView,
    *,
    uncertain_expected: set[str] | None = None,
) -> WireFaultVerdict:
    """Check the three invariants against a ledger view.

    `uncertain_expected` names the commands that were *known* to be uncertain
    during the run (a timeout was observed). Those must be recorded `unknown`:
    filing a timeout as `failed` is the specific mistake that invites a retry.
    """
    failures: list[str] = []

    duplicated = [command_id for command_id, count in view.starts.items() if count > 1]
    if duplicated:
        failures.append(
            "dispatched more than once (the robot may have moved twice): "
            + ", ".join(sorted(duplicated))
        )

    uncertain_ok = True
    for command_id in uncertain_expected or set():
        status = view.statuses.get(command_id)
        if status is None:
            uncertain_ok = False
            failures.append(f"{command_id} was uncertain but is absent from the ledger")
        elif status != "unknown":
            uncertain_ok = False
            failures.append(
                f"{command_id} was uncertain but is recorded {status!r}; a timeout is not a failure"
            )

    blocked_ok = _blocked_until_ack(view)
    if not blocked_ok:
        failures.append(
            "a side-effecting command was dispatched while an unresolved command was still "
            "unacknowledged"
        )

    return WireFaultVerdict(
        no_duplicate_effect=not duplicated,
        uncertain_stays_unknown=uncertain_ok,
        blocked_until_ack=blocked_ok,
        failures=failures,
    )


def _blocked_until_ack(view: LedgerView) -> bool:
    """No side effect may happen between an unacknowledged unknown and its ack.

    Reads the event stream in order: once a command is recorded `unknown` and
    has no acknowledgement, any later `started` event is a violation. A command
    that is itself the unknown one is fine — the timeout happens during its own
    start.
    """
    unresolved: set[str] = set()
    for command_id, kind in view.events:
        if kind == "acknowledged":
            unresolved.discard(command_id)
            continue
        if kind == "started" and unresolved - {command_id}:
            return False
        if view.statuses.get(command_id) == "unknown":
            if command_id not in view.acknowledged:
                unresolved.add(command_id)
    return True


__all__ = [
    "DDS_BASE_PORT",
    "DDS_PORT_SPAN",
    "SCENARIOS",
    "FaultScenario",
    "LedgerView",
    "NetemRule",
    "WireFaultVerdict",
    "build_commands",
    "check_ledger",
    "render",
]
