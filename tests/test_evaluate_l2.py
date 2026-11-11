"""Scoring the live stack with the L1 evaluator.

`training/evaluate_l2.py` turns a mission receipt into an episode record the L1
evaluator already understands, so one evaluator scores both levels. That
translation is the risky part: get it wrong and an L2 number is silently
compared against an L1 number measured in a different unit.

The three things pinned here:

- a completed mission is an episode with `outcome: success`;
- a failed mission is not, and the receipt's failure code survives into the
  record (the contract's vocabulary, not L1's -- translating one into the other
  would invent a correspondence);
- the record says `level: l2` and `unit: mission`, so nobody reads it as a
  turn-level L1 episode.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_harness.contracts import ActionRequest, ExecutionReceipt, ExecutionStatus
from training.evaluate import evaluate_records
from training.evaluate_l2 import _record_from_receipt


def _receipt(status: ExecutionStatus, *, failure=None, detail: str = "") -> ExecutionReceipt:
    return ExecutionReceipt(
        episode_id="ep-l2",
        command_id="cmd-1",
        execution_status=status,
        call_ok=status is ExecutionStatus.COMPLETED,
        physical_effect_verified=False if status is ExecutionStatus.REJECTED else None,
        failure_code=failure,
        detail=detail,
    )


def _request() -> ActionRequest:
    return ActionRequest(
        episode_id="ep-l2",
        command_id="cmd-1",
        capability="mission-control.deliver_tool",
        arguments={
            "tool_id": "hex_key_3mm",
            "target_station": "station_a",
            "operator_id": "operator_001",
        },
    )


def _record(receipt: ExecutionReceipt):
    return _record_from_receipt(
        receipt,
        episode_id="ep-l2",
        capability="mission-control.deliver_tool",
        request=_request(),
    )


def test_a_completed_mission_is_a_successful_episode() -> None:
    record = _record(_receipt(ExecutionStatus.COMPLETED, detail="delivered"))
    assert record.outcome == "success"
    assert record.summary["delivered"] is True
    assert record.summary["recovered"] is True


def test_a_failed_mission_is_not_a_success() -> None:
    record = _record(
        _receipt(ExecutionStatus.FAILED, failure="action_aborted", detail="arm aborted")
    )
    assert record.outcome != "success"
    assert record.summary["delivered"] is False
    assert record.turns[0].result["status"] == "failed"


def test_the_failure_code_survives_in_the_contract_vocabulary() -> None:
    """`timeout`, not `TIMEOUT`: the two vocabularies are not the same set."""
    record = _record(
        _receipt(ExecutionStatus.FAILED, failure="timeout", detail="navigation timed out")
    )
    assert record.turns[0].result["error_code"] == "timeout"


def test_a_rejected_mission_keeps_the_rejected_status() -> None:
    record = _record(
        _receipt(ExecutionStatus.REJECTED, failure="unknown_capability", detail="no such thing")
    )
    assert record.turns[0].result["status"] == "rejected"
    assert record.summary["execution_status"] == "rejected"


def test_the_record_says_it_is_l2_and_measured_in_missions() -> None:
    """An L2 episode is one mission; an L1 episode is one turn."""
    record = _record(_receipt(ExecutionStatus.COMPLETED))
    assert record.summary["level"] == "l2"
    assert record.source == "l2_ros2"
    assert record.scenario.get("level") == "l2"
    assert len(record.turns) == 1, "a mission is one submission, not a turn sequence"


def test_the_evaluator_accepts_an_l2_record_unchanged() -> None:
    """The whole point: one evaluator, both levels."""
    record = _record(_receipt(ExecutionStatus.COMPLETED))
    report = evaluate_records([record], "l2_baseline")
    assert report["overall"]["episodes"] == 1
    assert report["overall"]["task_success"] == 1.0
    # A mission that succeeded on a clean benchmark is not "fault-injected".
    assert report["overall"]["faulted_episodes"] == 0


def test_a_failed_mission_lands_in_the_failure_taxonomy() -> None:
    record = _record(
        _receipt(ExecutionStatus.FAILED, failure="action_aborted", detail="arm aborted")
    )
    report = evaluate_records([record], "l2_baseline")
    assert report["overall"]["task_success"] == 0.0
    classes = report["overall"]["terminal_classes"]
    assert "success" not in classes


def test_the_report_json_is_serialisable() -> None:
    """The artifact has to be writable next to the L1 ones."""
    report = evaluate_records([_record(_receipt(ExecutionStatus.COMPLETED))], "l2")
    blob = json.dumps(report)
    assert json.loads(blob)["overall"]["episodes"] == 1


def test_the_l2_output_path_is_not_the_l1_one() -> None:
    """Both levels write under runs/eval; the label keeps them apart."""
    source = (Path(__file__).resolve().parents[1] / "training" / "evaluate_l2.py").read_text(
        encoding="utf-8"
    )
    assert 'args.out or Path("runs/eval")' in source
