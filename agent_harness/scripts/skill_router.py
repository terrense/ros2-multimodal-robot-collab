#!/usr/bin/env python3
"""Small deterministic router for mission-plan JSON.

The script validates the high-level plan shape and prints the ROS2 commands
that a supervisor agent would dispatch. It is intentionally dependency-free so
it can run in a development shell before a ROS2 graph is available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REQUIRED_TOP_LEVEL = {"intent", "operator_id", "steps"}


def load_plan(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        plan = json.load(handle)
    missing = REQUIRED_TOP_LEVEL - set(plan)
    if missing:
        raise SystemExit(f"Invalid plan; missing fields: {sorted(missing)}")
    if not isinstance(plan["steps"], list):
        raise SystemExit("Invalid plan; steps must be a list")
    return plan


def render_command(step: dict) -> str:
    skill = step.get("skill")
    action = step.get("action")
    args = step.get("args", {})

    if skill == "mission-control" and action == "deliver_tool":
        return (
            "ros2 action send_goal /mission/deliver_tool "
            "robot_collab_interfaces/action/DeliverTool "
            f"\"{{tool_id: '{args['tool_id']}', target_station: '{args['target_station']}', "
            f"operator_id: '{args['operator_id']}', require_confirmation: true}}\" --feedback"
        )
    if skill == "perception" and action == "verify_operator":
        return (
            "ros2 action send_goal /skills/verify_operator "
            "robot_collab_interfaces/action/VerifyOperator "
            f"\"{{operator_id: '{args['operator_id']}', require_face_match: true}}\" --feedback"
        )
    if skill == "manipulation" and action == "publish_arm_control":
        return (
            "ros2 topic pub --once /arm/control_command "
            "robot_collab_interfaces/msg/ArmControlCommand "
            f"\"{{command: '{args['command']}', operator_id: '{args.get('operator_id', 'operator_001')}', "
            f"confidence: 1.0, emergency: {str(args.get('emergency', False)).lower()}, "
            f"detail: '{args.get('detail', '')}'}}\""
        )
    if skill == "system-state" and action == "query_state":
        return "ros2 service call /system/query_state robot_collab_interfaces/srv/QuerySystemState \"{requester: agent}\""
    return f"# No command renderer for {skill}.{action}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()

    plan = load_plan(args.plan)
    print(f"# intent: {plan['intent']}")
    for index, step in enumerate(plan["steps"], start=1):
        print(f"\n# step {index}: {step.get('skill')}.{step.get('action')}")
        print(render_command(step))


if __name__ == "__main__":
    main()
