"""Wire-fault acceptance criteria, netem rules, and the audit that decides them.

The criteria are written down in [`docs/specs/wire-faults.md`](../../docs/specs/wire-faults.md)
before the tool, so the tool can be checked against them rather than the other
way round. This module is that file in code.

The eight transport faults the runtime handles are injected in-process, against
a fake backend. That proves the *state machine*. It does not prove the
transport: a real link can lose a message after the robot moved and before
anyone heard about it.

Two kinds of criterion, and a run passes only if both hold:

    safety     what must never happen, however bad the link
    liveness   what must still happen under a moderate link, or the safety
               criteria are being satisfied by doing nothing

And a third kind that does not test the robot at all:

    setup      whether the measurement is real -- was the fault actually
               applied, was it cleared, was there a baseline to compare with

A failed setup criterion makes the run **void**: it says nothing about the
robot either way. Reporting a void run as a system failure is its own kind of
lie, and it is the common one -- a misconfigured injection looks exactly like a
system that survived a fault it never saw.
"""

from __future__ import annotations

import shlex
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

SAFETY = "safety"
LIVENESS = "liveness"
SETUP = "setup"


@dataclass(frozen=True)
class Criterion:
    id: str
    kind: str
    statement: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "kind": self.kind, "statement": self.statement}


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        "WF-S1",
        SAFETY,
        "no command id is dispatched more than once (no duplicate effect)",
    ),
    Criterion(
        "WF-S2",
        SAFETY,
        "a command whose outcome was uncertain is recorded unknown, never failed",
    ),
    Criterion(
        "WF-S3",
        SAFETY,
        "no command is dispatched while an unresolved command is unacknowledged",
    ),
    Criterion(
        "WF-S4",
        SAFETY,
        "every submitted command has a ledger row ending terminal or unknown",
    ),
    Criterion(
        "WF-S5",
        SAFETY,
        "no receipt claims a completed call that did not complete",
    ),
    Criterion(
        "WF-S6",
        SAFETY,
        "the harness never submits the same command id twice",
    ),
    Criterion(
        "WF-L1",
        LIVENESS,
        "under moderate loss, at least one command reaches a terminal state "
        "other than unknown",
    ),
    Criterion(
        "WF-L2",
        LIVENESS,
        "after the fault is cleared, a fresh command completes",
    ),
    Criterion(
        "WF-C1",
        SETUP,
        "the netem rule was in place before the first faulted dispatch",
    ),
    Criterion(
        "WF-C2",
        SETUP,
        "the netem rule is gone at the end of the run",
    ),
    Criterion(
        "WF-C3",
        SETUP,
        "a clean baseline run completed before the faulted one",
    ),
)

CRITERIA_BY_ID: dict[str, Criterion] = {criterion.id: criterion for criterion in CRITERIA}

#: At or below this loss, the stack is expected to make progress. Above it,
#: refusing to complete is the correct behaviour and liveness does not apply.
MODERATE_LOSS_PERCENT = 20.0

#: Statuses that mean "this command will not move again on its own".
TERMINAL_STATUSES = frozenset({"completed", "failed", "rejected", "unknown"})


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    VOID = "void"


@dataclass
class CriterionResult:
    criterion: Criterion
    held: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.criterion.id,
            "kind": self.criterion.kind,
            "statement": self.criterion.statement,
            "held": self.held,
            "detail": self.detail,
        }


@dataclass
class WireFaultReport:
    results: list[CriterionResult] = field(default_factory=list)

    def result(self, criterion_id: str) -> CriterionResult | None:
        for result in self.results:
            if result.criterion.id == criterion_id:
                return result
        return None

    @property
    def failed_setup(self) -> list[CriterionResult]:
        return [r for r in self.results if r.criterion.kind == SETUP and not r.held]

    @property
    def failed_safety(self) -> list[CriterionResult]:
        return [r for r in self.results if r.criterion.kind == SAFETY and not r.held]

    @property
    def failed_liveness(self) -> list[CriterionResult]:
        return [r for r in self.results if r.criterion.kind == LIVENESS and not r.held]

    @property
    def verdict(self) -> Verdict:
        if self.failed_setup:
            return Verdict.VOID
        if self.failed_safety or self.failed_liveness:
            return Verdict.FAIL
        return Verdict.PASS

    def report(self) -> str:
        lines = [f"wire fault verdict: {self.verdict.value.upper()}"]
        for kind in (SETUP, SAFETY, LIVENESS):
            lines.append(f"  {kind}:")
            for result in self.results:
                if result.criterion.kind != kind:
                    continue
                mark = "ok" if result.held else "VIOLATED"
                line = f"    {result.criterion.id} {result.criterion.statement}: {mark}"
                if result.detail:
                    line += f" ({result.detail})"
                lines.append(line)
        if self.verdict is Verdict.VOID:
            lines.append(
                "  a void run says nothing about the robot: fix the harness and repeat"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "results": [result.to_dict() for result in self.results],
        }


# --------------------------------------------------------------- netem rules


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
        if high != low:
            raise ValueError(
                "tc u32 cannot express a port range portably; pass the single "
                "port to target, or omit port_range to cover the whole interface"
            )
        apply.append(
            [
                binary, "filter", "add", "dev", interface, "protocol", "ip",
                "parent", "1:0", "prio", "3", "u32",
                "match", "ip", "dport", str(low), "0xffff",
                "flowid", "1:3",
            ]
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


def probe_netem(
    interface: str = "lo",
    *,
    binary: str = "tc",
    runner: Callable[[list[str]], str] | None = None,
) -> bool:
    """Is a netem rule in place on this interface right now?

    This is what makes WF-C1 and WF-C2 more than an assumption: the tool reads
    the queueing discipline back rather than trusting that its own `tc` call
    worked.

    The whole tree is read, not just the root. The rule is installed as a
    `prio` root qdisc with the netem on band `1:3`, so `tc qdisc show dev lo
    root` prints the prio band and nothing else -- and the probe cannot see
    the rule it is meant to confirm. The first real run came back VOID for
    exactly that reason: the fault had been injected, and the probe said it
    was not there.
    """
    run = runner or _run_capture
    try:
        output = run([binary, "qdisc", "show", "dev", interface])
    except OSError:
        return False
    return "netem" in output


def _run_capture(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.stdout or ""


# ---------------------------------------------------------------- the audit


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
    #: command_id -> the receipt the ledger stored.
    receipts: dict[str, dict[str, Any]] = field(default_factory=dict)

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
            view.receipts[row.command_id] = dict(getattr(row, "receipt", {}) or {})
            if getattr(row, "reconciled_by", None):
                view.acknowledged.add(row.command_id)
        for command_id, kind in ledger.all_events():
            view.events.append((command_id, kind))
            if kind == "started":
                view.starts[command_id] += 1
        return view


@dataclass
class RunFacts:
    """What the harness knows about the run it just did."""

    #: Command ids the harness submitted, in order.
    submitted: list[str] = field(default_factory=list)
    #: Command ids whose outcome the harness saw as uncertain.
    uncertain: set[str] = field(default_factory=set)
    #: Status of the clean baseline dispatch.
    baseline_status: str | None = None
    #: Status of the dispatch after the fault was cleared.
    recovery_status: str | None = None
    #: Whether the netem rule was observed before the faulted dispatch.
    fault_applied: bool = False
    #: Whether the netem rule was observed gone at the end.
    fault_cleared: bool = False
    #: The loss level in force, for the liveness threshold.
    loss_percent: float = 0.0


def evaluate_wire_fault(view: LedgerView, facts: RunFacts) -> WireFaultReport:
    """Decide every criterion, and therefore the verdict."""
    results: list[CriterionResult] = []

    duplicated = sorted(cid for cid, count in view.starts.items() if count > 1)
    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-S1"],
            held=not duplicated,
            detail=("dispatched more than once: " + ", ".join(duplicated)) if duplicated else "",
        )
    )

    wrong = [
        f"{cid} is {view.statuses.get(cid)!r}"
        for cid in sorted(facts.uncertain)
        if view.statuses.get(cid) not in (None, "unknown")
    ]
    missing = [cid for cid in sorted(facts.uncertain) if cid not in view.statuses]
    detail = "; ".join(wrong + [f"{cid} is absent from the ledger" for cid in missing])
    results.append(CriterionResult(CRITERIA_BY_ID["WF-S2"], held=not wrong and not missing, detail=detail))

    blocked = _blocked_until_ack(view)
    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-S3"],
            held=blocked,
            detail="" if blocked else "a command was dispatched while an unresolved command was unacknowledged",
        )
    )

    incomplete = [
        cid for cid in facts.submitted if view.statuses.get(cid) not in TERMINAL_STATUSES
    ]
    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-S4"],
            held=not incomplete,
            detail=("no terminal or unknown row: " + ", ".join(sorted(incomplete))) if incomplete else "",
        )
    )

    false_success = [
        cid
        for cid, receipt in view.receipts.items()
        if str(receipt.get("execution_status", "")) == "completed"
        and receipt.get("call_ok") is False
    ]
    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-S5"],
            held=not false_success,
            detail=("completed but call_ok is false: " + ", ".join(sorted(false_success)))
            if false_success
            else "",
        )
    )

    resubmitted = [cid for cid, count in Counter(facts.submitted).items() if count > 1]
    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-S6"],
            held=not resubmitted,
            detail=("submitted more than once by the harness: " + ", ".join(sorted(resubmitted)))
            if resubmitted
            else "",
        )
    )

    # Liveness only applies where the link is expected to be survivable.
    if facts.loss_percent <= MODERATE_LOSS_PERCENT:
        progressed = [
            cid
            for cid in facts.submitted
            if view.statuses.get(cid) in ("completed", "failed", "rejected")
        ]
        results.append(
            CriterionResult(
                CRITERIA_BY_ID["WF-L1"],
                held=bool(progressed),
                detail="" if progressed else "no command reached a terminal state other than unknown",
            )
        )
    else:
        results.append(
            CriterionResult(
                CRITERIA_BY_ID["WF-L1"],
                held=True,
                detail=(
                    f"not applicable: {facts.loss_percent:g}% loss is beyond the "
                    f"{MODERATE_LOSS_PERCENT:g}% where progress is expected"
                ),
            )
        )

    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-L2"],
            held=facts.recovery_status == "completed",
            detail=(
                ""
                if facts.recovery_status == "completed"
                else f"the command after the fault was cleared ended {facts.recovery_status!r}"
            ),
        )
    )

    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-C1"],
            held=facts.fault_applied,
            detail="" if facts.fault_applied else "no netem rule was observed before the faulted dispatch",
        )
    )
    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-C2"],
            held=facts.fault_cleared,
            detail="" if facts.fault_cleared else "the netem rule was still in place at the end",
        )
    )
    results.append(
        CriterionResult(
            CRITERIA_BY_ID["WF-C3"],
            held=facts.baseline_status == "completed",
            detail=(
                ""
                if facts.baseline_status == "completed"
                else f"the clean baseline ended {facts.baseline_status!r}"
            ),
        )
    )

    return WireFaultReport(results=results)


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
        if view.statuses.get(command_id) == "unknown" and command_id not in view.acknowledged:
            unresolved.add(command_id)
    return True


__all__ = [
    "CRITERIA",
    "CRITERIA_BY_ID",
    "LIVENESS",
    "MODERATE_LOSS_PERCENT",
    "SAFETY",
    "SCENARIOS",
    "SETUP",
    "Criterion",
    "CriterionResult",
    "FaultScenario",
    "LedgerView",
    "NetemRule",
    "RunFacts",
    "Verdict",
    "WireFaultReport",
    "build_commands",
    "evaluate_wire_fault",
    "probe_netem",
    "render",
]
