#!/usr/bin/env python3
"""Evaluate the live (L2) stack with the same evaluator as L1.

    python -m training.scripts.evaluate_l2 --label l2_baseline --missions 20 \
        --out runs/eval/l2_baseline.json --ledger runs/l2_ledger.db

The live stack exposes one composite capability -- ``mission-control.deliver_tool``
-- so an L2 episode is one mission, not one skill call. That is a real
difference from L1 and it is stated in the output rather than smoothed over:
the episode has one turn, and the unit of comparison is the mission.

Everything else is shared with L1: the same evaluator, the same report
generator, the same run records, the same ledger. An L2 number and an L1
number can therefore sit in the same table -- with the header saying which
fault families have been calibrated against the live stack.

The receipt metrics become reachable here, and only here: a live mission
produces receipts, and a trajectory cannot.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

MISSION_CAPABILITY = "mission-control.deliver_tool"


def _build_client(executor: Any, registry: Any, ledger_path: Path | None, timeout_s: float):
    from agent_harness.contracts import CapabilityRegistry
    from agent_harness.runtime import ExecutionLedger, ToolClient
    from agent_harness.runtime.ros_backend import RosBackend

    ledger = None
    if ledger_path is not None:
        ledger = ExecutionLedger(ledger_path)
        orphans = ledger.mark_orphans()
        if orphans:
            print(f"# ledger: {orphans} orphaned command(s) from a previous run")
    client = ToolClient(
        RosBackend(executor, registry),
        CapabilityRegistry.load(REPO_ROOT),
        default_timeout_s=timeout_s,
        max_workers=1,
        ledger=ledger,
    )
    return client, ledger


def _record_from_receipt(receipt: Any, *, episode_id: str, capability: str, request: Any) -> Any:
    """One mission, as an episode record the L1 evaluator understands."""
    from agent_env.protocol import Outcome
    from training.envs.multi_turn_env import EpisodeRecord, TurnRecord

    status = str(getattr(receipt.execution_status, "value", receipt.execution_status))
    detail = str(getattr(receipt, "detail", "") or "")
    succeeded = status == "completed"
    result = {
        "status": "succeeded" if succeeded else ("rejected" if status == "rejected" else "failed"),
        "error_code": (
            getattr(receipt.failure_code, "value", receipt.failure_code)
            if getattr(receipt, "failure_code", None)
            else "OK"
        ),
        "message": detail,
    }
    turn = TurnRecord(
        turn_index=1,
        observation={"level": "l2", "capability": capability},
        assistant_text="",
        parse_status="ok",
        action={"skill": "mission-control", "action": "deliver_tool", "args": dict(request.arguments)},
        result=result,
        reward_components={},
        info={"phase": "DONE" if succeeded else "IDLE", "progress": 1.0 if succeeded else 0.0},
        invalid=False,
        malformed=False,
    )
    summary = {
        "outcome": Outcome.SUCCESS.value if succeeded else Outcome.WRONG_TERMINATION.value,
        "steps": 1,
        "invalid_actions": 0,
        "malformed_actions": 0,
        "repaired_actions": 0,
        "safety_violations": 0,
        "grounding_violations": 0,
        "recovered": succeeded,
        "delivered": succeeded,
        "faults": [],
        "expected_abort": False,
        "level": "l2",
        "execution_status": status,
        "failure_reason": detail,
    }
    return EpisodeRecord(
        episode_id=episode_id,
        scenario={"level": "l2", "capability": capability},
        seed=0,
        source="l2_ros2",
        turns=[turn],
        summary=summary,
        messages=[],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default="l2_baseline")
    parser.add_argument("--missions", type=int, default=10)
    parser.add_argument("--out", type=Path, default=None, help="evaluation JSON, same shape as training.evaluate")
    parser.add_argument("--ledger", type=Path, default=None, help="durable command ledger")
    parser.add_argument("--run-record", type=Path, default=None, help="where to write the run record")
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    parser.add_argument("--tool-id", default="wrench")
    parser.add_argument("--target-station", default="station_a")
    parser.add_argument("--operator-id", default="operator_001")
    parser.add_argument("--benchmark", type=Path, default=REPO_ROOT / "benchmarks" / "fault_injection_v1.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; dispatch nothing")
    args = parser.parse_args()

    from agent_harness.contracts import ActionRequest, new_command_id
    from agent_harness.scripts.ros_tool_executor import RosToolExecutor, SkillRegistry
    from training.evaluate import evaluate_records
    from training.receipt_metrics import receipt_metrics

    registry = SkillRegistry.load(SKILLS_DIR)
    arguments = {
        "tool_id": args.tool_id,
        "target_station": args.target_station,
        "operator_id": args.operator_id,
    }
    if args.dry_run:
        print(json.dumps({"capability": MISSION_CAPABILITY, "arguments": arguments}, indent=2))
        return

    executor = RosToolExecutor(timeout_sec=args.timeout_sec)
    client, ledger = _build_client(executor, registry, args.ledger, args.timeout_sec)
    records = []
    receipt_rows: list[dict[str, Any]] = []
    try:
        for index in range(args.missions):
            episode_id = f"l2-{uuid.uuid4().hex[:10]}"
            request = ActionRequest(
                episode_id=episode_id,
                command_id=new_command_id(),
                capability=MISSION_CAPABILITY,
                arguments=dict(arguments),
                timeout_s=args.timeout_sec,
            )
            submitted = client.submit(request)
            receipt = (
                client.wait(request.command_id)
                if str(getattr(submitted.execution_status, "value", submitted.execution_status)) == "queued"
                else submitted
            )
            row = receipt.to_dict()
            row["capability"] = MISSION_CAPABILITY
            receipt_rows.append(row)
            records.append(
                _record_from_receipt(
                    receipt, episode_id=episode_id, capability=MISSION_CAPABILITY, request=request
                )
            )
            print(f"  mission {index + 1}/{args.missions}: {row.get('execution_status')}")
    finally:
        client.shutdown(30.0)
        if ledger is not None:
            ledger.close()
        executor.close()

    report = evaluate_records(records, args.label)
    report["receipts"] = receipt_metrics(receipt_rows)
    report["level"] = "l2"
    report["unit"] = "mission"

    out = args.out or Path("runs/eval") / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"-> {out}")

    if args.run_record is not None:
        from training.run_record import write_run_record

        write_run_record(
            name=args.label,
            kind="eval",
            model_id="l2_ros2_stack",
            benchmark=args.benchmark,
            policy=MISSION_CAPABILITY,
            episodes=args.missions,
            artifacts=[str(out)],
            # No model, therefore no prompt: the provenance hashes the ROS
            # capability catalogue instead, and carries an empty-prompt marker
            # so an L2 record can never be mistaken for an L1 one.
            catalogue="ros2",
            out=args.run_record,
        )
        print(f"-> {args.run_record}")


if __name__ == "__main__":
    main()
