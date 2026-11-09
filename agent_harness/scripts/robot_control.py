#!/usr/bin/env python3
"""Operator control CLI: the paths that must not depend on a model.

    ack        resolve an orphan after a human checked the robot   (no ROS needed)
    pause      ask the arm to pause
    stop       request a stop and fail closed
    resume     clear fail-closed, with a note

``stop`` is a request to the arm controller, not a hardware emergency stop.
A real E-stop is a circuit.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Run as a script: the package root is not on sys.path by default.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _ledger(args):
    from agent_harness.runtime import ExecutionLedger

    return ExecutionLedger(args.ledger)


def _operator_client(args):
    """Build a client over the live ROS 2 backend. Requires a sourced workspace."""
    from agent_harness.contracts import CapabilityRegistry
    from agent_harness.runtime import ToolClient
    from agent_harness.runtime.ros_backend import RosBackend
    from agent_harness.scripts.ros_tool_executor import RosToolExecutor, SkillRegistry

    skills = SkillRegistry.load(REPO_ROOT / "skills")
    executor = RosToolExecutor(timeout_sec=args.timeout_sec)
    client = ToolClient(
        RosBackend(executor, skills),
        CapabilityRegistry.load(REPO_ROOT),
        default_timeout_s=args.timeout_sec,
        ledger=_ledger(args),
    )
    return client, executor


def _run_operator_action(args) -> int:
    from agent_harness.runtime import RobotControl

    client, executor = _operator_client(args)
    control = RobotControl(
        client, episode_id=args.episode_id or f"control-{uuid.uuid4().hex[:8]}", operator=args.operator
    )
    try:
        if args.action == "pause":
            receipt = control.pause()
        elif args.action == "stop":
            receipt = control.stop(reason=args.reason)
        else:
            control.resume(note=args.note)
            print("fail-closed cleared")
            return 0
        final = client.wait(receipt.command_id, timeout=args.timeout_sec * 2)
        print(f"{args.action}: {final.execution_status.value} {final.detail}".strip())
        return 0 if final.execution_status.value in {"completed", "queued"} else 1
    finally:
        client.shutdown(5.0)
        executor.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operator control paths for the robot runtime.")
    sub = parser.add_subparsers(dest="action", required=True)

    def common(p):
        p.add_argument("--ledger", type=Path, default=REPO_ROOT / "runs" / "ledger.db")
        p.add_argument("--operator", required=True)

    pause = sub.add_parser("pause", help="Ask the arm to pause")
    common(pause)
    pause.add_argument("--episode-id")
    pause.add_argument("--timeout-sec", type=float, default=5.0)

    stop = sub.add_parser("stop", help="Request a stop and fail closed")
    common(stop)
    stop.add_argument("--episode-id")
    stop.add_argument("--reason", required=True)
    stop.add_argument("--timeout-sec", type=float, default=5.0)

    resume = sub.add_parser("resume", help="Clear fail-closed after an operator check")
    common(resume)
    resume.add_argument("--note", required=True)
    resume.add_argument("--episode-id")
    resume.add_argument("--timeout-sec", type=float, default=5.0)

    ack = sub.add_parser("ack", help="Resolve an orphan command (no ROS required)")
    common(ack)
    ack.add_argument("--command-id", required=True)
    ack.add_argument("--note", required=True)

    args = parser.parse_args(argv)

    if args.action == "ack":
        ledger = _ledger(args)
        try:
            record = ledger.acknowledge_unknown(
                args.command_id, operator=args.operator, note=args.note
            )
        except Exception as error:  # noqa: BLE001 - CLI reports, never crashes
            print(f"error: {error}", file=sys.stderr)
            return 1
        finally:
            ledger.close()
        print(f"{record.command_id}: {record.status.value} (reconciled by {record.reconciled_by})")
        return 0

    return _run_operator_action(args)


if __name__ == "__main__":
    raise SystemExit(main())
