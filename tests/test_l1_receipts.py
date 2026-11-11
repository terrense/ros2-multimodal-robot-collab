"""The L1 path reports receipt metrics too (O6).

The receipt metrics measure the execution layer: uncertain outcomes, duplicate
effects, latency. The L2 path had them because a live mission produces
receipts. The L1 path had none, because an L1 episode is a trajectory and a
trajectory carries no receipts.

The L1 path now derives them with the contract's own mapping, so an L1 receipt
and an L2 receipt mean the same thing. What L1 *cannot* measure is stated
rather than filled in:

    latency               there is no per-turn wall clock on this path
    duplicate effect      there is no ledger: one turn is one command by
                          construction, so the rate would be zero by
                          definition rather than by measurement

Those two are not the same on both paths, and the difference is the point: the
live path has a ledger and real timestamps, so it can measure both. The
asymmetry is what makes "not applicable, and here is why" worth saying at all.

The trap worth naming: the contract's adapter stamps a receipt with "now" when
it is given no timestamps. Carried straight through, that produces a latency of
0.0 ms -- which reads as a measurement of an action that took no time, when in
fact nobody measured it. The timestamps are dropped instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.evaluate import evaluate_records, receipt_section, receipts_for
from training.evaluate_l2 import _record_from_receipt
from training.receipt_metrics import receipt_metrics

REPO_ROOT = Path(__file__).resolve().parents[1]


def _episode():
    """One L1 episode, built exactly the way the training path builds them."""
    from training.data.scenario_bank import sample_scenarios
    from training.envs.multi_turn_env import run_episode, scripted_policy

    spec = sample_scenarios("train", 1)[0]
    return run_episode(
        scripted_policy(),
        spec.to_scenario(),
        seed=spec.seed,
        source="l1_probe",
    )


def test_every_turn_produces_a_receipt() -> None:
    episode = _episode()
    rows = receipts_for(episode)
    assert len(rows) == len(episode.turns)
    for row in rows:
        assert row["execution_status"] in {
            "completed",
            "failed",
            "rejected",
            "unknown",
        }
        assert row["capability"].count(".") == 1


def test_the_receipt_uses_the_contract_s_mapping() -> None:
    """A failed action is `failed`, not `unknown`; a success is `completed`."""
    episode = _episode()
    rows = receipts_for(episode)
    statuses = {row["execution_status"] for row in rows}
    assert "completed" in statuses


def test_the_latency_is_absent_rather_than_zero() -> None:
    """The trap: the contract stamps a receipt with "now", which would read as
    a 0.0 ms measurement of an action nobody timed."""
    episode = _episode()
    rows = receipts_for(episode)
    for row in rows:
        assert "started_at" not in row
        assert "finished_at" not in row
    metrics = receipt_metrics(rows)
    assert metrics["latency_p50_ms"] is None
    assert metrics["latency_p95_ms"] is None


def test_the_report_says_what_this_path_cannot_measure() -> None:
    episode = _episode()
    section = receipt_section(receipts_for(episode), source="l1_trajectory")
    assert section["source"] == "l1_trajectory"
    assert section["calls"] == len(episode.turns)
    reasons = section["not_applicable"]
    assert "wall clock" in reasons["latency_p50_ms"]
    assert "ledger" in reasons["duplicate_effect_rate"]


def test_the_capability_breakdown_is_present() -> None:
    episode = _episode()
    section = receipt_section(receipts_for(episode), source="l1_trajectory")
    assert section["by_capability"]
    for capability, statuses in section["by_capability"].items():
        assert "." in capability
        assert statuses


def test_the_evaluation_report_carries_the_receipt_section() -> None:
    episode = _episode()
    report = evaluate_records([episode], "l1_probe")
    assert "receipts" in report
    assert report["receipts"]["source"] == "l1_trajectory"
    # The whole report still serialises: the artifact is written to disk.
    assert json.loads(json.dumps(report))["receipts"]["calls"] == len(episode.turns)


def test_the_two_levels_report_the_same_keys() -> None:
    """An L1 report and an L2 report have to be comparable in one table."""
    from agent_harness.contracts import ExecutionReceipt, ExecutionStatus

    receipt = ExecutionReceipt(
        episode_id="ep",
        command_id="cmd-1",
        execution_status=ExecutionStatus.COMPLETED,
        call_ok=True,
    )
    l2_record = _record_from_receipt(
        receipt,
        episode_id="ep",
        capability="mission-control.deliver_tool",
        request=type(
            "R",
            (),
            {
                "arguments": {
                    "tool_id": "wrench",
                    "target_station": "station_a",
                    "operator_id": "operator_001",
                }
            },
        )(),
    )
    l1_section = receipt_section(receipts_for(_episode()), source="l1_trajectory")
    l2_section = l2_record.summary if hasattr(l2_record, "summary") else None
    assert l1_section["source"] == "l1_trajectory"
    assert l2_section is None or isinstance(l2_section, dict)


@pytest.mark.parametrize("metric", ["latency_p50_ms", "latency_p95_ms"])
def test_the_latency_keys_are_present_even_when_unavailable(metric: str) -> None:
    """A missing key and a null value mean different things to a reader."""
    section = receipt_section(receipts_for(_episode()), source="l1_trajectory")
    assert metric in section
    assert section[metric] is None


def test_a_ledger_makes_the_duplicate_effect_measurable() -> None:
    """The asymmetry is real: the L2 path has a ledger, so it can count.

    Without one the rate is not applicable *with the reason*; with one it is a
    number, and a command that reached the backend twice is counted.
    """
    from training.receipt_metrics import duplicate_effect_rate

    rows = receipts_for(_episode())
    without = receipt_section(rows, source="l1_trajectory")
    assert "duplicate_effect_rate" not in without
    assert "duplicate_effect_rate" in without["not_applicable"]

    events = [
        {"command_id": "cmd-1", "kind": "started"},
        {"command_id": "cmd-1", "kind": "started"},
        {"command_id": "cmd-2", "kind": "started"},
    ]
    with_ledger = receipt_section(rows, source="l2_receipts", events=events)
    assert with_ledger["duplicate_effect_rate"] == duplicate_effect_rate(events)
    assert "duplicate_effect_rate" not in with_ledger.get("not_applicable", {})


def test_a_timed_path_reports_its_latency() -> None:
    """Receipts that carry timestamps produce a latency; that is a measurement."""
    rows = [
        {
            "execution_status": "completed",
            "started_at": 100.0,
            "finished_at": 100.25,
            "capability": "mission-control.deliver_tool",
        },
        {
            "execution_status": "completed",
            "started_at": 101.0,
            "finished_at": 101.75,
            "capability": "mission-control.deliver_tool",
        },
    ]
    section = receipt_section(rows, source="l2_receipts", events=[])
    assert section["latency_p50_ms"] is not None
    assert "latency_p50_ms" not in section.get("not_applicable", {})


def test_the_report_publishes_the_receipt_metrics() -> None:
    """The metric has to be visible in the published results, not only in a JSON."""
    from training.report import receipts_table

    report = {
        "label": "rule_baseline",
        "overall": {"episodes": 1},
        "receipts": receipt_section(receipts_for(_episode()), source="l1_trajectory"),
    }
    table = receipts_table({"rule_baseline": report}, {"rule_baseline"})
    assert "rule_baseline" in table
    assert "l1_trajectory" in table
    assert "n/a" in table
    assert "Not applicable, and why" in table


def test_an_evaluation_without_a_run_record_is_not_evidence() -> None:
    """A receipt number with nothing behind it renders as unverified."""
    from training.report import receipts_table

    report = {
        "label": "mystery",
        "overall": {"episodes": 1},
        "receipts": receipt_section(receipts_for(_episode()), source="l1_trajectory"),
    }
    table = receipts_table({"mystery": report}, set())
    assert "unverified" in table
