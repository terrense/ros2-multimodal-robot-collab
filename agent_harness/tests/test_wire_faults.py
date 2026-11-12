"""The wire-fault criteria, one test per criterion.

The criteria live in `docs/specs/wire-faults.md`; this file checks that the
implementation decides them the way the document says. Every criterion has a
case where it holds and a case where it does not, because a check that cannot
fail is not a check.

The whole file runs without ROS 2, without root and without a robot: the
ledger is a view, and the netem probe takes an injected runner.
"""

from __future__ import annotations

from collections import Counter

import pytest

from agent_harness.runtime.wire_faults import (
    CRITERIA,
    CRITERIA_BY_ID,
    MODERATE_LOSS_PERCENT,
    SCENARIOS,
    LedgerView,
    NetemRule,
    RunFacts,
    Verdict,
    build_commands,
    evaluate_wire_fault,
    probe_netem,
    render,
)


def _view(
    statuses: dict[str, str],
    events: list[tuple[str, str]],
    *,
    acknowledged: set[str] | None = None,
    receipts: dict[str, dict] | None = None,
) -> LedgerView:
    starts = Counter(command_id for command_id, kind in events if kind == "started")
    return LedgerView(
        statuses=statuses,
        starts=starts,
        events=events,
        acknowledged=acknowledged or set(),
        receipts=receipts or {},
    )


def _facts(**overrides) -> RunFacts:
    """A run where everything held, so each test can break one thing."""
    base = {
        "submitted": ["cmd-1"],
        "uncertain": set(),
        "baseline_status": "completed",
        "recovery_status": "completed",
        "fault_applied": True,
        "fault_cleared": True,
        "loss_percent": 15.0,
        # The fault has to cost time, or the run says nothing about the robot.
        "baseline_seconds": 1.0,
        "faulted_seconds": 3.0,
        "recovery_seconds": 1.0,
    }
    base.update(overrides)
    return RunFacts(**base)


def _held(report, criterion_id: str) -> bool:
    result = report.result(criterion_id)
    assert result is not None, f"{criterion_id} was not decided"
    return result.held


# ------------------------------------------------------------ the criteria


def test_every_criterion_is_written_down_and_decided() -> None:
    assert len(CRITERIA) == 12
    ids = [criterion.id for criterion in CRITERIA]
    assert len(set(ids)) == len(ids)
    for criterion in CRITERIA:
        assert criterion.statement
        assert criterion.kind in {"safety", "liveness", "setup"}


def test_a_clean_run_passes_everything() -> None:
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    report = evaluate_wire_fault(view, _facts())
    assert report.verdict is Verdict.PASS
    assert "PASS" in report.report()


# ------------------------------------------------------------------- safety


def test_s1_a_command_dispatched_twice_is_caught() -> None:
    """The failure the whole ledger exists to prevent: the robot moves twice."""
    view = _view(
        {"cmd-1": "completed"},
        [
            ("cmd-1", "submitted"),
            ("cmd-1", "started"),
            ("cmd-1", "started"),
            ("cmd-1", "finished"),
        ],
    )
    report = evaluate_wire_fault(view, _facts())
    assert not _held(report, "WF-S1")
    assert report.verdict is Verdict.FAIL


def test_s2_a_timeout_recorded_as_failed_is_caught() -> None:
    """`failed` is a claim that it did not happen. A timeout cannot make it."""
    view = _view(
        {"cmd-1": "failed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    report = evaluate_wire_fault(view, _facts(uncertain={"cmd-1"}))
    assert not _held(report, "WF-S2")
    assert "not a failure" in (report.result("WF-S2").detail or "") or "failed" in (
        report.result("WF-S2").detail or ""
    )


def test_s2_a_timeout_recorded_as_unknown_is_correct() -> None:
    view = _view(
        {"cmd-1": "unknown"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    assert _held(evaluate_wire_fault(view, _facts(uncertain={"cmd-1"})), "WF-S2")


def test_s3_dispatching_while_an_unknown_is_outstanding_is_caught() -> None:
    view = _view(
        {"cmd-1": "unknown", "cmd-2": "completed"},
        [
            ("cmd-1", "submitted"),
            ("cmd-1", "started"),
            ("cmd-1", "finished"),
            ("cmd-2", "submitted"),
            ("cmd-2", "started"),
        ],
    )
    assert not _held(evaluate_wire_fault(view, _facts(submitted=["cmd-1", "cmd-2"])), "WF-S3")


def test_s3_dispatching_after_the_acknowledgement_is_allowed() -> None:
    view = _view(
        {"cmd-1": "unknown", "cmd-2": "completed"},
        [
            ("cmd-1", "submitted"),
            ("cmd-1", "started"),
            ("cmd-1", "finished"),
            ("cmd-1", "acknowledged"),
            ("cmd-2", "submitted"),
            ("cmd-2", "started"),
        ],
        acknowledged={"cmd-1"},
    )
    assert _held(evaluate_wire_fault(view, _facts(submitted=["cmd-1", "cmd-2"])), "WF-S3")


def test_s3_a_command_does_not_block_itself() -> None:
    view = _view(
        {"cmd-1": "unknown"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    assert _held(evaluate_wire_fault(view, _facts(uncertain={"cmd-1"})), "WF-S3")


def test_s4_a_command_without_a_terminal_row_is_caught() -> None:
    """A command that vanished from the ledger is indistinguishable from one
    that never ran."""
    view = _view({"cmd-1": "queued"}, [("cmd-1", "submitted")])
    report = evaluate_wire_fault(view, _facts(submitted=["cmd-1"]))
    assert not _held(report, "WF-S4")
    assert "no terminal or unknown row" in (report.result("WF-S4").detail or "")


def test_s5_a_completed_receipt_that_did_not_complete_is_caught() -> None:
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
        receipts={"cmd-1": {"execution_status": "completed", "call_ok": False}},
    )
    assert not _held(evaluate_wire_fault(view, _facts()), "WF-S5")


def test_s6_the_harness_resubmitting_a_command_is_caught() -> None:
    """A harness that retries a timed-out command is the bug, not the fix.

    WF-S6 is about the harness; WF-S1 is about the ledger. They are different
    failures and the test shows the difference: here the harness submitted the
    same id twice, but the backend only ever started it once, so the ledger
    shows no duplicate effect while the harness is still at fault.
    """
    view = _view(
        {"cmd-1": "unknown"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    report = evaluate_wire_fault(view, _facts(submitted=["cmd-1", "cmd-1"], uncertain={"cmd-1"}))
    assert not _held(report, "WF-S6")
    assert _held(report, "WF-S1"), "the ledger recorded one start, so no duplicate effect"


def test_s1_and_s6_both_fail_when_the_duplicate_reached_the_backend() -> None:
    view = _view(
        {"cmd-1": "unknown"},
        [
            ("cmd-1", "submitted"),
            ("cmd-1", "started"),
            ("cmd-1", "started"),
            ("cmd-1", "finished"),
        ],
    )
    report = evaluate_wire_fault(view, _facts(submitted=["cmd-1", "cmd-1"], uncertain={"cmd-1"}))
    assert not _held(report, "WF-S1")
    assert not _held(report, "WF-S6")


# ----------------------------------------------------------------- liveness


def test_l1_needs_progress_only_where_progress_is_expected() -> None:
    """A system that refuses everything satisfies safety and is useless."""
    view = _view(
        {"cmd-1": "unknown"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    moderate = evaluate_wire_fault(view, _facts(uncertain={"cmd-1"}, loss_percent=15.0))
    assert not _held(moderate, "WF-L1")

    severe = evaluate_wire_fault(view, _facts(uncertain={"cmd-1"}, loss_percent=60.0))
    assert _held(severe, "WF-L1")
    assert "not applicable" in (severe.result("WF-L1").detail or "")


def test_l1_holds_when_something_did_reach_a_terminal_state() -> None:
    view = _view(
        {"cmd-1": "unknown", "cmd-2": "completed"},
        [
            ("cmd-1", "submitted"),
            ("cmd-1", "started"),
            ("cmd-1", "finished"),
            ("cmd-2", "submitted"),
            ("cmd-2", "started"),
            ("cmd-2", "finished"),
        ],
    )
    facts = _facts(submitted=["cmd-1", "cmd-2"], uncertain={"cmd-1"}, loss_percent=15.0)
    assert _held(evaluate_wire_fault(view, facts), "WF-L1")


def test_l2_a_recovery_after_the_fault_must_complete() -> None:
    view = _view(
        {"cmd-1": "unknown"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    report = evaluate_wire_fault(view, _facts(uncertain={"cmd-1"}, recovery_status="unknown"))
    assert not _held(report, "WF-L2")


# -------------------------------------------------------------------- setup


def test_c1_a_fault_that_was_never_applied_makes_the_run_void() -> None:
    """Measuring the clean path and reporting it as the faulted path."""
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    report = evaluate_wire_fault(view, _facts(fault_applied=False))
    assert not _held(report, "WF-C1")
    assert report.verdict is Verdict.VOID
    assert "says nothing about the robot" in report.report()


def test_c2_a_fault_left_behind_makes_the_run_void() -> None:
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    report = evaluate_wire_fault(view, _facts(fault_cleared=False))
    assert not _held(report, "WF-C2")
    assert report.verdict is Verdict.VOID


def test_c3_a_missing_baseline_makes_the_run_void() -> None:
    """30% success is good or bad only relative to something."""
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    report = evaluate_wire_fault(view, _facts(baseline_status="failed"))
    assert not _held(report, "WF-C3")
    assert report.verdict is Verdict.VOID


def test_a_setup_failure_outranks_a_safety_failure() -> None:
    """A void run says nothing, so it is not reported as a system failure."""
    view = _view(
        {"cmd-1": "completed"},
        [
            ("cmd-1", "submitted"),
            ("cmd-1", "started"),
            ("cmd-1", "started"),
            ("cmd-1", "finished"),
        ],
    )
    report = evaluate_wire_fault(view, _facts(fault_applied=False))
    assert report.failed_safety  # the safety criterion did fail
    assert report.verdict is Verdict.VOID  # but the run is void regardless


# --------------------------------------------------------------- netem rules


def test_an_empty_rule_says_so() -> None:
    rule = NetemRule()
    assert rule.empty
    assert rule.describe() == "no fault"


def test_loss_carries_its_correlation() -> None:
    """Real links lose in bursts; tc takes correlation as a percentage of loss."""
    assert NetemRule(loss_percent=20.0, correlation_percent=75.0).netem_arguments() == [
        "loss",
        "20%",
        "75%",
    ]


def test_delay_carries_its_jitter() -> None:
    assert NetemRule(delay_ms=400.0, jitter_ms=120.0).netem_arguments() == [
        "delay",
        "400ms",
        "120ms",
    ]


def test_applying_a_rule_clears_first_so_a_stale_rule_cannot_stack() -> None:
    commands = build_commands(NetemRule(loss_percent=10.0), interface="lo")["apply"]
    assert commands[0][:5] == ["tc", "qdisc", "del", "dev", "lo"]
    assert commands[1][:6] == ["tc", "qdisc", "add", "dev", "lo", "root"]


def test_clearing_is_one_command_and_targets_the_same_interface() -> None:
    commands = build_commands(NetemRule(loss_percent=10.0), interface="eth0")
    assert commands["clear"] == [["tc", "qdisc", "del", "dev", "eth0", "root"]]


def test_a_port_range_is_refused_rather_than_silently_approximated() -> None:
    """tc u32 has no portable range syntax; approximating would miss traffic."""
    with pytest.raises(ValueError, match="range"):
        build_commands(NetemRule(loss_percent=10.0), port_range=(7400, 7650))


def test_every_scenario_is_described_and_expects_something() -> None:
    for name, scenario in SCENARIOS.items():
        assert scenario.name == name
        assert not scenario.rule.empty
        assert scenario.expectation
        assert scenario.capability
        assert scenario.arguments


def test_the_report_renders_shell_safe_commands() -> None:
    assert render(build_commands(NetemRule(loss_percent=10.0))["clear"]).startswith(
        "tc qdisc del dev lo root"
    )


# ------------------------------------------------------------- the probe


def test_the_probe_reads_the_queueing_discipline_back() -> None:
    """Trusting that our own `tc` call worked is how a void run gets reported
    as a system failure."""
    assert probe_netem("lo", runner=lambda command: "netem 30% 25%") is True
    assert probe_netem("lo", runner=lambda command: "qdisc noqueue 0: ") is False


def test_the_probe_looks_at_the_whole_queueing_discipline() -> None:
    """The netem rule is a child of a prio qdisc, not the root itself.

    `tc qdisc show dev lo root` prints the prio band and nothing else, so the
    probe cannot see the rule it is supposed to confirm -- and a run that
    really did inject the fault comes back VOID. The first real run came back
    exactly that way.
    """
    seen: dict[str, list[str]] = {}

    def runner(command):
        seen["command"] = list(command)
        return (
            "qdisc prio 1: root refcnt 2\n"
            "qdisc netem 30: parent 1:3 limit 1000 loss 30% 25%"
        )

    assert probe_netem("lo", runner=runner) is True
    assert "root" not in seen["command"], "the whole tree has to be read"


def test_a_fault_that_costs_no_time_voids_the_run() -> None:
    """The fault has to reach the transport, or the run says nothing.

    A lost packet is retransmitted rather than dropped, so a reliable
    transport survives loss by taking longer -- not by failing. Without this
    criterion, a run in which the injected loss never reached a single packet
    passes every other criterion: nothing was uncertain, nothing was
    duplicated, and the recovery worked, because the link was healthy.
    """
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    facts = _facts(baseline_seconds=2.2, faulted_seconds=2.25)
    report = evaluate_wire_fault(view, facts)
    assert not _held(report, "WF-C4")
    assert report.verdict is Verdict.VOID, "a fault that never arrived proves nothing"


def test_a_mild_fault_that_costs_time_counts() -> None:
    """Thirty per cent loss costs about a fifth of a second here.

    The bar is "measurably slower than the healthy dispatch", not "slow enough
    to fail": a mild fault is still a real fault, and a run that threw it away
    would have been throwing away the measurement.
    """
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    facts = _facts(baseline_seconds=2.2, faulted_seconds=2.63)
    report = evaluate_wire_fault(view, facts)
    assert _held(report, "WF-C4")
    assert report.verdict is Verdict.PASS


def test_a_fault_that_costs_time_is_accepted() -> None:
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    facts = _facts(baseline_seconds=1.0, faulted_seconds=4.0)
    report = evaluate_wire_fault(view, facts)
    assert _held(report, "WF-C4")
    assert report.verdict is Verdict.PASS


def test_the_probe_reports_failure_rather_than_raising() -> None:
    def explode(command):
        raise OSError("no such file")

    assert probe_netem("lo", runner=explode) is False


def test_the_moderate_loss_threshold_is_the_documented_one() -> None:
    assert MODERATE_LOSS_PERCENT == 20.0
    assert CRITERIA_BY_ID["WF-L1"].kind == "liveness"
