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
import threading
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

    def names(self) -> list[str]:
        """Every `skill.action` the registry defines, including non-callable ones."""
        out: list[str] = []
        for skill, schema in sorted(self._skills.items()):
            for action in sorted((schema.get("actions") or {})):
                out.append(f"{skill}.{action}")
        return out

    def catalogue(self) -> dict[str, dict[str, Any]]:
        """The whole registry as `skill.action` -> schema, non-callable included.

        Provenance hashes this rather than only the callable subset: a change to
        a perception-internal action still changes what the runtime is built
        against, and `get_action` refuses those by design.
        """
        return {
            f"{skill}.{action}": dict(spec)
            for skill, schema in sorted(self._skills.items())
            for action, spec in sorted((schema.get("actions") or {}).items())
        }

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
    def __init__(self, timeout_sec: float, *, concurrent: bool = False) -> None:
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

        # Concurrency is opt-in: the default path is exactly what it was.
        self._executor = None
        self._executor_thread = None
        self._callback_groups: dict[str, Any] = {}
        if concurrent:
            self._start_concurrent_executor()

    # ------------------------------------------------------- concurrency

    def _start_concurrent_executor(self) -> None:
        """Spin the node from a MultiThreadedExecutor in a background thread.

        One callback group per resource lets the base and the arm make progress
        at the same time. With a single-threaded executor those callbacks would
        still be serialised, so starting the thread without the groups would
        buy wall-clock without buying concurrency.
        """
        from rclpy.callback_groups import ReentrantCallbackGroup
        from rclpy.executors import MultiThreadedExecutor

        self._callback_group_type = ReentrantCallbackGroup
        self._executor = MultiThreadedExecutor(num_threads=4)
        self._executor.add_node(self.node)
        self._executor_thread = threading.Thread(
            target=self._executor.spin, name="ros-executor", daemon=True
        )
        self._executor_thread.start()

    @property
    def concurrent(self) -> bool:
        return self._executor is not None

    @property
    def max_workers(self) -> int:
        """Scheduler workers this executor can actually keep busy."""
        return 4 if self._executor is not None else 1

    def callback_group(self, resource_key: str | None) -> Any:
        """The callback group for a resource, or None in serial mode."""
        if self._executor is None or not resource_key:
            return None
        group = self._callback_groups.get(resource_key)
        if group is None:
            group = self._callback_group_type()
            self._callback_groups[resource_key] = group
        return group

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(timeout_sec=2.0)
            self._executor = None
        self.node.destroy_node()
        self.rclpy.shutdown()

    def _spin_until_done(self, future, timeout_sec: float) -> bool:
        """Wait for a future, driving the node only when nothing else does.

        With a background MultiThreadedExecutor the node is already spun from
        another thread; spinning here too would put two spinners on one node
        and make callback ordering unpredictable.
        """
        if self._executor is not None:
            from agent_harness.runtime.concurrency import wait_for_future

            return wait_for_future(future, timeout_sec)

        started = time.monotonic()
        while not future.done():
            self.rclpy.spin_once(self.node, timeout_sec=0.1)
            if timeout_sec > 0.0 and time.monotonic() - started > timeout_sec:
                return False
        return True

    def _execute_action(
        self,
        result: ToolCallResult,
        timeout_sec: float | None = None,
        resource_key: str | None = None,
    ) -> None:
        timeout = self.timeout_sec if timeout_sec is None else timeout_sec
        action_type = import_ros_type(result.ros_type)
        group = self.callback_group(resource_key)
        client = (
            self.ActionClient(self.node, action_type, result.endpoint, callback_group=group)
            if group is not None
            else self.ActionClient(self.node, action_type, result.endpoint)
        )
        if not client.wait_for_server(timeout_sec=timeout):
            raise RuntimeError(f"Action server unavailable: {result.endpoint}")

        goal = action_type.Goal()
        assign_fields(goal, result.payload)

        def feedback_callback(feedback_msg) -> None:
            feedback = feedback_msg.feedback
            result.feedback.append(ros_message_to_dict(feedback))

        send_future = client.send_goal_async(goal, feedback_callback=feedback_callback)
        if not self._spin_until_done(send_future, timeout):
            raise RuntimeError(f"Timed out sending action goal: {result.endpoint}")
        goal_handle = send_future.result()
        if not goal_handle.accepted:
            result.status = "rejected"
            result.message = f"Action goal rejected: {result.endpoint}"
            return

        result_future = goal_handle.get_result_async()
        if not self._spin_until_done(result_future, timeout):
            cancel_future = goal_handle.cancel_goal_async()
            self._spin_until_done(cancel_future, 2.0)
            raise RuntimeError(f"Timed out waiting for action result: {result.endpoint}")

        response = result_future.result()
        # Goal status 4 = SUCCEEDED; anything else (aborted/canceled) must stop the plan.
        result.status = "succeeded" if getattr(response, "status", 0) == 4 else "failed"
        result.result = {
            "status": getattr(response, "status", None),
            "result": ros_message_to_dict(getattr(response, "result", None)),
        }
        # Correlate the receipt with the ROS 2 goal. Verified against rclpy at
        # runtime; the executor never interprets the uuid.
        goal_id = getattr(goal_handle, "goal_id", None)
        goal_uuid = getattr(goal_id, "uuid", None)
        if goal_uuid is not None:
            try:
                result.result["goal_uuid"] = bytes(goal_uuid).hex()
            except (TypeError, ValueError):
                pass
        result.message = f"Action completed: {result.endpoint}"

    def _execute_service(
        self,
        result: ToolCallResult,
        timeout_sec: float | None = None,
        resource_key: str | None = None,
    ) -> None:
        timeout = self.timeout_sec if timeout_sec is None else timeout_sec
        service_type = import_ros_type(result.ros_type)
        group = self.callback_group(resource_key)
        client = (
            self.node.create_client(service_type, result.endpoint, callback_group=group)
            if group is not None
            else self.node.create_client(service_type, result.endpoint)
        )
        if not client.wait_for_service(timeout_sec=timeout):
            raise RuntimeError(f"Service unavailable: {result.endpoint}")
        request = service_type.Request()
        assign_fields(request, result.payload)
        future = client.call_async(request)
        if not self._spin_until_done(future, timeout):
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
            if self._executor is None:
                # Serial mode: this thread is the only spinner on the node, so
                # it has to pump it for the message to be processed.
                #
                # Concurrent mode: the background MultiThreadedExecutor is
                # already spinning this node, and a second spinner on one node
                # is exactly the mistake the concurrency work warns about. It
                # is not theoretical -- publishing an arm control command while
                # a motion was in flight starved the executor, and the motion's
                # goal response did not come back until the call timed out.
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


def _status_text(status) -> str:
    from agent_harness.contracts import ExecutionStatus

    return {
        ExecutionStatus.COMPLETED: "succeeded",
        ExecutionStatus.FAILED: "failed",
        ExecutionStatus.REJECTED: "rejected",
        ExecutionStatus.UNKNOWN: "unknown",
    }.get(status, "failed")


def execute_plan_runtime(
    plan: dict[str, Any],
    calls: list[SkillCall],
    registry: SkillRegistry,
    executor: "RosToolExecutor",
    timeout_sec: float,
    ledger_path: Path | None = None,
) -> list[ToolCallResult]:
    """Execute a validated plan through the local runtime, one submission per step.

    The plan stays the unit of *intent*; each step becomes an ``ActionRequest``
    and returns an ``ExecutionReceipt``. Steps are still sequential here — a
    plan is a macro over submissions, not a parallel program.

    With ``ledger_path``, every submission is recorded before dispatch and the
    ROS goal uuid is backfilled into the ledger from the receipt.
    """
    from agent_harness.contracts import (
        ActionRequest,
        CapabilityRegistry,
        ExecutionStatus,
        new_command_id,
    )
    from agent_harness.runtime import ExecutionLedger, ToolClient
    from agent_harness.runtime.ros_backend import RosBackend

    ledger = None
    if ledger_path is not None:
        ledger = ExecutionLedger(ledger_path)
        # Anything left running by a previous process blocks side effects until
        # an operator acknowledges it (see robot_control.py ack).
        orphans = ledger.mark_orphans()
        if orphans:
            print(f"# ledger: {orphans} orphaned command(s) from a previous run")

    trace_id = str(plan.get("trace_id") or f"trace-{uuid.uuid4().hex[:10]}")
    # A plan is a sequence of steps; the backend still keys each one by its
    # resource, so a plan whose steps touch different resources can overlap
    # once the executor is running concurrently.
    client = ToolClient(
        RosBackend(executor, registry),
        CapabilityRegistry.load(REPO_ROOT),
        default_timeout_s=timeout_sec,
        max_workers=getattr(executor, "max_workers", 1),
        ledger=ledger,
    )
    results: list[ToolCallResult] = []
    try:
        for call in calls:
            schema = registry.get_action(call.skill, call.action)
            transport, endpoint, ros_type = transport_for(schema)
            payload = payload_for_call(call)
            action_timeout = schema.get("timeout_sec")
            request = ActionRequest(
                episode_id=trace_id,
                command_id=new_command_id(),
                capability=f"{call.skill}.{call.action}",
                arguments=dict(call.args),
                timeout_s=float(action_timeout) if action_timeout else None,
            )
            submitted = client.submit(request)
            receipt = (
                client.wait(request.command_id)
                if submitted.execution_status is ExecutionStatus.QUEUED
                else submitted
            )
            results.append(
                ToolCallResult(
                    step_index=call.index,
                    skill=call.skill,
                    action=call.action,
                    trace_id=trace_id,
                    mode="execute",
                    status=_status_text(receipt.execution_status),
                    transport=transport,
                    endpoint=endpoint,
                    ros_type=ros_type,
                    message=receipt.detail,
                    command=render_ros_command(transport, endpoint, ros_type, payload),
                    payload=payload,
                    feedback=list(receipt.result.get("feedback", [])),
                    result={
                        "status": receipt.execution_status.value,
                        "failure_code": (
                            receipt.failure_code.value if receipt.failure_code else None
                        ),
                        "result": receipt.result.get("result"),
                    },
                )
            )
            if receipt.execution_status in {
                ExecutionStatus.FAILED,
                ExecutionStatus.REJECTED,
                ExecutionStatus.UNKNOWN,
            }:
                break
    finally:
        client.shutdown(30.0)
        if ledger is not None:
            ledger.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and dispatch agent mission-plan tool calls.")
    parser.add_argument("plan", type=Path)
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument("--mode", choices=["dry-run", "execute"], default="dry-run")
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="execute mode only: durable command ledger (SQLite). Recommended for any real run.",
    )
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help=(
            "execute mode only: spin the node from a MultiThreadedExecutor with one "
            "callback group per resource, so the base and the arm can move at once"
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print structured JSON results.")
    args = parser.parse_args()

    registry = SkillRegistry.load(args.skills_dir)
    plan = load_plan(args.plan)
    calls = registry.validate_plan(plan)

    if args.mode == "dry-run":
        results = dry_run_plan(plan, calls, registry)
    else:
        executor = RosToolExecutor(timeout_sec=args.timeout_sec, concurrent=args.concurrent)
        try:
            # One path only. The pre-runtime loop dispatched straight to the
            # executor: no ledger, no reconciliation, no resource scheduling.
            # That made it the one way to move the robot and leave no evidence,
            # which is the failure this whole runtime exists to prevent.
            results = execute_plan_runtime(
                plan, calls, registry, executor, args.timeout_sec, args.ledger
            )
        finally:
            executor.close()

    if args.json:
        print(json.dumps([asdict(item) for item in results], indent=2, ensure_ascii=False))
    else:
        print_human(results)

    if any(item.status in {"failed", "rejected", "unknown"} for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
