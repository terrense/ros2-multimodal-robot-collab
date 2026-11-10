"""Wire-fault injection: the rules it builds, and the invariants it checks.

The in-process faults prove the state machine. These cover the other half: what
must stay true after real packets are lost. The checks are pure functions over
a ledger view, so they run without ROS 2, without root, and without a robot.

The three invariants each have a specific failure they prevent:

    no_duplicate_effect      a timed-out command must not be dispatched twice;
                             the robot would move twice
    uncertain_stays_unknown  a timeout recorded as `failed` invites the retry
    blocked_until_ack        an unresolved command must block every later side
                             effect until a human acknowledges it
"""

from __future__ import annotations

from collections import Counter

import pytest

from agent_harness.runtime.wire_faults import (
    SCENARIOS,
    LedgerView,
    NetemRule,
    build_commands,
    check_ledger,
    render,
)

# ------------------------------------------------------------- netem rules


def test_an_empty_rule_says_so() -> None:
    rule = NetemRule()
    assert rule.empty
    assert rule.describe() == "no fault"
    assert build_commands(rule, interface="lo")["apply"][2][-1] == "netem"


def test_loss_carries_its_correlation() -> None:
    """Real links lose in bursts; tc takes correlation as a percentage of loss."""
    args = NetemRule(loss_percent=20.0, correlation_percent=75.0).netem_arguments()
    assert args == ["loss", "20%", "75%"]


def test_delay_carries_its_jitter() -> None:
    args = NetemRule(delay_ms=400.0, jitter_ms=120.0).netem_arguments()
    assert args == ["delay", "400ms", "120ms"]


def test_the_scenarios_produce_the_parameters_they_name() -> None:
    assert NetemRule(loss_percent=30.0).netem_arguments() == ["loss", "30%"]
    assert NetemRule(duplicate_percent=20.0).netem_arguments() == ["duplicate", "20%"]
    assert NetemRule(reorder_percent=10.0).netem_arguments() == ["reorder", "10%"]


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


def test_a_single_port_filters_on_it() -> None:
    commands = build_commands(NetemRule(loss_percent=10.0), port_range=(7400, 7400))["apply"]
    filter_command = commands[-1]
    assert "dport" in filter_command
    assert "7400" in filter_command


def test_every_scenario_is_described_and_expects_something() -> None:
    for name, scenario in SCENARIOS.items():
        assert scenario.name == name
        assert not scenario.rule.empty
        assert scenario.expectation
        assert scenario.capability


def test_the_report_renders_shell_safe_commands() -> None:
    text = render(build_commands(NetemRule(loss_percent=10.0))["clear"])
    assert text.startswith("tc qdisc del dev lo root")


# ------------------------------------------------------------ the invariants


def _view(
    statuses: dict[str, str],
    events: list[tuple[str, str]],
    *,
    acknowledged: set[str] | None = None,
) -> LedgerView:
    starts = Counter(command_id for command_id, kind in events if kind == "started")
    return LedgerView(
        statuses=statuses,
        starts=starts,
        events=events,
        acknowledged=acknowledged or set(),
    )


def test_a_clean_run_survives() -> None:
    view = _view(
        {"cmd-1": "completed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    verdict = check_ledger(view)
    assert verdict.survived
    assert "PASS" in verdict.report()


def test_a_command_dispatched_twice_is_caught() -> None:
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
    verdict = check_ledger(view)
    assert not verdict.no_duplicate_effect
    assert not verdict.survived
    assert any("moved twice" in failure for failure in verdict.failures)


def test_a_timeout_recorded_as_failed_is_a_violation() -> None:
    """`failed` is a claim that it did not happen. A timeout cannot make it."""
    view = _view(
        {"cmd-1": "failed"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    verdict = check_ledger(view, uncertain_expected={"cmd-1"})
    assert not verdict.uncertain_stays_unknown
    assert any("not a failure" in failure for failure in verdict.failures)


def test_a_timeout_recorded_as_unknown_is_correct() -> None:
    view = _view(
        {"cmd-1": "unknown"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    assert check_ledger(view, uncertain_expected={"cmd-1"}).uncertain_stays_unknown


def test_an_uncertain_command_missing_from_the_ledger_is_a_violation() -> None:
    verdict = check_ledger(_view({}, []), uncertain_expected={"cmd-1"})
    assert not verdict.uncertain_stays_unknown
    assert any("absent" in failure for failure in verdict.failures)


def test_dispatching_while_an_unknown_is_outstanding_is_caught() -> None:
    """Fail-closed: an unresolved command blocks every later side effect."""
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
    verdict = check_ledger(view)
    assert not verdict.blocked_until_ack
    assert any("unacknowledged" in failure for failure in verdict.failures)


def test_dispatching_after_the_acknowledgement_is_allowed() -> None:
    """A human decision unblocks the machine; that is what ack is for."""
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
    assert check_ledger(view).blocked_until_ack


def test_a_command_does_not_block_itself() -> None:
    """The unknown is discovered *during* cmd-1's own start; that is not a block."""
    view = _view(
        {"cmd-1": "unknown"},
        [("cmd-1", "submitted"), ("cmd-1", "started"), ("cmd-1", "finished")],
    )
    assert check_ledger(view).blocked_until_ack


def test_the_report_names_which_invariant_broke() -> None:
    view = _view({"cmd-1": "completed"}, [("cmd-1", "started"), ("cmd-1", "started")])
    report = check_ledger(view).report()
    assert "FAIL" in report
    assert "no duplicate effect:" in report
    assert "violated" in report
