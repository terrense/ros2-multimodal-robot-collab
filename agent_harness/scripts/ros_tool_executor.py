#!/usr/bin/env python3
"""Validate and dispatch agent tool calls into ROS2.

The executor is intentionally useful in two modes:

- dry-run: validate a mission plan and print the ROS2 command/payload that
  would be dispatched. This mode has no ROS2 dependency.
- execute: import rclpy and call ROS2 Actions, Services, and Topics directly.

This is the first real bridge between LLM/function-calling shaped JSON and the
ROS2 skill contracts in this repository.
"""

from __future__ import annotations

import argparse
import importlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKILLS_DIR = REPO_ROOT / "skills"
REQUIRED_TOP_LEVEL = {"intent", "operator_id", "steps"}


@dataclass(frozen=True)
class SkillCall:
    index: int
    skill: str
    action: str
    args: dict[str, Any]


@dataclass
class ToolCallResult:
    step_index: int
    skill: str
    action: str
    trace_id: str
    mode: str
    status: str
    transport: str
    endpoint: str
    ros_type: str
    message: str
    command: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    feedback: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)


class PlanValidationError(ValueError):
    """Raised when a mission plan cannot be routed safely."""


class SkillRegistry:
    def __init__(self, skills: dict[str, dict[str, Any]]) -> None:
        self._skills = skills

    @classmethod
    def load(cls, skills_dir: Path) -> "SkillRegistry":
        skills: dict[str, dict[str, Any]] = {}
        for schema_path in sorted(skills_dir.glob("*/schema.json")):
            with schema_path.open("r", encoding="utf-8") as handle:
                schema = json.load(handle)
            name = schema.get("name") or schema_path.parent.name
            skills[name] = schema
        if not skills:
            raise PlanValidationError(f"No skill schemas found under {skills_dir}")
        return cls(skills)

    def get_action(self, skill: str, action: str) -> dict[str, Any]:
        skill_schema = self._skills.get(skill)
        if skill_schema is None:
            raise PlanValidationError(f"Unknown skill: {skill}")
        action_schema = (skill_schema.get("actions") or {}).get(action)
        if action_schema is None:
            raise PlanValidationError(f"Unknown action: {skill}.{action}")
        if action_schema.get("callable", True) is False:
            raise PlanValidationError(f"Skill action is not callable by the agent: {skill}.{action}")
        return action_schema

    def validate_plan(self, plan: dict[str, Any]) -> list[SkillCall]:
        missing = REQUIRED_TOP_LEVEL - set(plan)
        if missing:
            raise PlanValidationError(f"Invalid plan; missing fields: {sorted(missing)}")
        if not isinstance(plan["steps"], list) or not plan["steps"]:
            raise PlanValidationError("Invalid plan; steps must be a non-empty list")

        calls: list[SkillCall] = []
        for index, step in enumerate(plan["steps"], start=1):
            if not isinstance(step, dict):
                raise PlanValidationError(f"Invalid step {index}; step must be an object")
            skill = step.get("skill")
            action = step.get("action")
            args = step.get("args", {})
            if not skill or not action:
                raise PlanValidationError(f"Invalid step {index}; missing skill or action")
            if not isinstance(args, dict):
                raise PlanValidationError(f"Invalid step {index}; args must be an object")

            action_schema = self.get_action(skill, action)
            normalized = normalize_args(skill, action, args, plan)
            required = set(action_schema.get("required", []))
            missing_args = required - set(normalized)
            if missing_args:
                raise PlanValidationError(
                    f"Invalid step {index} {skill}.{action}; missing args: {sorted(missing_args)}"
                )

            commands = action_schema.get("commands")
            if commands and normalized.get("command") not in commands:
                raise PlanValidationError(
                    f"Invalid step {index} {skill}.{action}; command must be one of {commands}"
                )

            calls.append(SkillCall(index=index, skill=skill, action=action, args=normalized))
        return calls


def load_plan(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_args(skill: str, action: str, args: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    operator_id = str(plan.get("operator_id", "operator_001"))
    safety = plan.get("safety") or {}

    if skill == "mission-control" and action == "deliver_tool":
        normalized.setdefault("operator_id", operator_id)
        normalized.setdefault("require_confirmation", bool(safety.get("requires_handover_confirmation", True)))
    elif skill == "perception" and action == "verify_operator":
        normalized.setdefault("require_face_match", bool(safety.get("requires_face_auth", True)))
    elif skill == "manipulation" and action == "publish_arm_control":
        normalized.setdefault("operator_id", operator_id)
        normalized.setdefault("source", "agent_harness")
        normalized.setdefault("confidence", 1.0)
        normalized.setdefault("emergency", normalized.get("command") == "system_stop")
        normalized.setdefault("detail", "")
    elif skill == "hri" and action == "publish_tts":
        normalized.setdefault("source", "agent_harness")
    elif skill == "system-state" and action == "query_state":
        normalized.setdefault("requester", "agent_harness")

    return normalized


def transport_for(action_schema: dict[str, Any]) -> tuple[str, str, str]:
    if "ros_action" in action_schema:
        return "action", str(action_schema["ros_action"]), str(action_schema["ros_type"])
    if "ros_service" in action_schema:
        return "service", str(action_schema["ros_service"]), str(action_schema["ros_type"])
    if "ros_topic" in action_schema:
        return "topic", str(action_schema["ros_topic"]), str(action_schema["ros_type"])
    raise PlanValidationError("Skill action has no ROS endpoint")


def payload_for_call(call: SkillCall) -> dict[str, Any]:
    args = dict(call.args)
    if call.skill == "hri" and call.action == "publish_tts":
        return {"data": args["text"]}
    if call.skill == "manipulation" and call.action == "publish_arm_control":
        return {
            "command": args["command"],
            "operator_id": args["operator_id"],
            "source": args["source"],
            "confidence": float(args["confidence"]),
            "emergency": bool(args["emergency"]),
            "detail": args.get("detail", ""),
        }
    return args


def render_ros_command(transport: str, endpoint: str, ros_type: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False)
    if transport == "action":
        return f"ros2 action send_goal {endpoint} {ros_type} '{body}' --feedback"
    if transport == "service":
        return f"ros2 service call {endpoint} {ros_type} '{body}'"
    if transport == "topic":
        return f"ros2 topic pub --once {endpoint} {ros_type} '{body}'"
    raise PlanValidationError(f"Unknown transport: {transport}")


def dry_run_plan(plan: dict[str, Any], calls: list[SkillCall], registry: SkillRegistry) -> list[ToolCallResult]:
    trace_id = str(plan.get("trace_id") or f"trace-{uuid.uuid4().hex[:10]}")
    results: list[ToolCallResult] = []
    for call in calls:
        action_schema = registry.get_action(call.skill, call.action)
        transport, endpoint, ros_type = transport_for(action_schema)
        payload = payload_for_call(call)
        results.append(
            ToolCallResult(
                step_index=call.index,
                skill=call.skill,
                action=call.action,
                trace_id=trace_id,
                mode="dry-run",
                status="validated",
                transport=transport,
                endpoint=endpoint,
                ros_type=ros_type,
                command=render_ros_command(transport, endpoint, ros_type, payload),
                payload=payload,
                message="Validated; no ROS2 call dispatched.",
                result={
                    "risk": action_schema.get("risk", "unknown"),
                    "requires_confirmation": bool(action_schema.get("requires_confirmation", False)),
                },
            )
        )
    return results


def import_ros_type(ros_type: str):
    parts = ros_type.split("/")
    if len(parts) != 3:
        raise RuntimeError(f"Unsupported ROS type string: {ros_type}")
    package, namespace, type_name = parts
    module = importlib.import_module(f"{package}.{namespace}")
    return getattr(module, type_name)


def assign_fields(message: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if not hasattr(message, key):
            raise RuntimeError(f"{type(message).__name__} has no field '{key}'")
        current = getattr(message, key)
        if isinstance(value, dict) and hasattr(current, "__slots__"):
            assign_fields(current, value)
        else:
            setattr(message, key, value)


def ros_message_to_dict(message: Any) -> Any:
    if message is None:
        return None
    if isinstance(message, (bool, int, float, str)):
        return message
    if isinstance(message, (list, tuple)):
        return [ros_message_to_dict(item) for item in message]
    slots = getattr(message, "__slots__", None)
    if not slots:
        return str(message)
    output: dict[str, Any] = {}
    for slot in slots:
        field = slot[1:] if slot.startswith("_") else slot
        output[field] = ros_message_to_dict(getattr(message, field))
    return output


class RosToolExecutor:
    def __init__(self, timeout_sec: float) -> None:
        try:
            import rclpy
            from rclpy.action import ActionClient
            from rclpy.node import Node
        except ImportError as exc:
            raise RuntimeError(
                "rclpy is required for --mode execute. Source ROS2 first, e.g. "
                "'source /opt/ros/humble/setup.bash && source install/setup.bash'."
            ) from exc

        self.rclpy = rclpy
        self.ActionClient = ActionClient
        self.Node = Node
        self.timeout_sec = timeout_sec
        self.rclpy.init(args=None)
        self.node = self.Node("agent_harness_tool_executor")

    def close(self) -> None:
        self.node.destroy_node()
        self.rclpy.shutdown()

    def execute_plan(
        self,
        plan: dict[str, Any],
        calls: list[SkillCall],
        registry: SkillRegistry,
    ) -> list[ToolCallResult]:
        trace_id = str(plan.get("trace_id") or f"trace-{uuid.uuid4().hex[:10]}")
        results: list[ToolCallResult] = []
        for call in calls:
            action_schema = registry.get_action(call.skill, call.action)
            transport, endpoint, ros_type = transport_for(action_schema)
            payload = payload_for_call(call)
            command = render_ros_command(transport, endpoint, ros_type, payload)
            result = ToolCallResult(
                step_index=call.index,
                skill=call.skill,
                action=call.action,
                trace_id=trace_id,
                mode="execute",
                status="pending",
                transport=transport,
                endpoint=endpoint,
                ros_type=ros_type,
                command=command,
                payload=payload,
                message="Dispatch pending.",
            )
            try:
                if transport == "action":
                    self._execute_action(result)
                elif transport == "service":
                    self._execute_service(result)
                elif transport == "topic":
                    self._execute_topic(result)
                else:
                    raise RuntimeError(f"Unsupported transport: {transport}")
            except Exception as exc:  # noqa: BLE001 - convert ROS/runtime failures into tool results.
                result.status = "failed"
                result.message = str(exc)
            results.append(result)
            if result.status == "failed":
                break
        return results

    def _spin_until_done(self, future, timeout_sec: float) -> bool:
        started = time.monotonic()
        while not future.done():
            self.rclpy.spin_once(self.node, timeout_sec=0.1)
            if timeout_sec > 0.0 and time.monotonic() - started > timeout_sec:
                return False
        return True

    def _execute_action(self, result: ToolCallResult) -> None:
        action_type = import_ros_type(result.ros_type)
        client = self.ActionClient(self.node, action_type, result.endpoint)
        if not client.wait_for_server(timeout_sec=self.timeout_sec):
            raise RuntimeError(f"Action server unavailable: {result.endpoint}")

        goal = action_type.Goal()
        assign_fields(goal, result.payload)

        def feedback_callback(feedback_msg) -> None:
            feedback = feedback_msg.feedback
            result.feedback.append(ros_message_to_dict(feedback))

        send_future = client.send_goal_async(goal, feedback_callback=feedback_callback)
        if not self._spin_until_done(send_future, self.timeout_sec):
            raise RuntimeError(f"Timed out sending action goal: {result.endpoint}")
        goal_handle = send_future.result()
        if not goal_handle.accepted:
            result.status = "rejected"
            result.message = f"Action goal rejected: {result.endpoint}"
            return

        result_future = goal_handle.get_result_async()
        if not self._spin_until_done(result_future, self.timeout_sec):
            cancel_future = goal_handle.cancel_goal_async()
            self._spin_until_done(cancel_future, 2.0)
            raise RuntimeError(f"Timed out waiting for action result: {result.endpoint}")

        response = result_future.result()
        result.status = "succeeded" if getattr(response, "status", 0) == 4 else "finished"
        result.result = {
            "status": getattr(response, "status", None),
            "result": ros_message_to_dict(getattr(response, "result", None)),
        }
        result.message = f"Action completed: {result.endpoint}"

    def _execute_service(self, result: ToolCallResult) -> None:
        service_type = import_ros_type(result.ros_type)
        client = self.node.create_client(service_type, result.endpoint)
        if not client.wait_for_service(timeout_sec=self.timeout_sec):
            raise RuntimeError(f"Service unavailable: {result.endpoint}")
        request = service_type.Request()
        assign_fields(request, result.payload)
        future = client.call_async(request)
        if not self._spin_until_done(future, self.timeout_sec):
            raise RuntimeError(f"Timed out waiting for service result: {result.endpoint}")
        result.status = "succeeded"
        result.result = {"response": ros_message_to_dict(future.result())}
        result.message = f"Service completed: {result.endpoint}"

    def _execute_topic(self, result: ToolCallResult) -> None:
        message_type = import_ros_type(result.ros_type)
        publisher = self.node.create_publisher(message_type, result.endpoint, 10)
        message = message_type()
        assign_fields(message, result.payload)
        for _ in range(3):
            publisher.publish(message)
            self.rclpy.spin_once(self.node, timeout_sec=0.1)
        result.status = "succeeded"
        result.result = {"published": True}
        result.message = f"Topic message published: {result.endpoint}"


def print_human(results: list[ToolCallResult]) -> None:
    if results:
        print(f"# trace_id: {results[0].trace_id}")
    for item in results:
        print(f"\n# step {item.step_index}: {item.skill}.{item.action}")
        print(f"# status: {item.status}")
        print(f"# transport: {item.transport} {item.endpoint}")
        if item.command:
            print(item.command)
        if item.mode == "execute":
            print(f"# message: {item.message}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and dispatch agent mission-plan tool calls.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument("--mode", choices=["dry-run", "execute"], default="dry-run")
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--json", action="store_true", help="Print structured JSON results.")
    args = parser.parse_args()

    registry = SkillRegistry.load(args.skills_dir)
    plan = load_plan(args.plan)
    calls = registry.validate_plan(plan)

    if args.mode == "dry-run":
        results = dry_run_plan(plan, calls, registry)
    else:
        executor = RosToolExecutor(timeout_sec=args.timeout_sec)
        try:
            results = executor.execute_plan(plan, calls, registry)
        finally:
            executor.close()

    if args.json:
        print(json.dumps([asdict(item) for item in results], indent=2, ensure_ascii=False))
    else:
        print_human(results)

    if any(item.status in {"failed", "rejected"} for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
