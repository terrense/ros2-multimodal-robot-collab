"""ROS 2 backend: the existing executor, behind the model-neutral interface.

Deliberately thin. It does not re-implement action/service/topic dispatch; it
looks up the transport in the skill schema and delegates to the methods the
executor already had. That is what keeps the dry-run path and the execute path
from drifting apart.

Two honesty rules:

- an action returning SUCCEEDED is ``completed`` with
  ``physical_effect_verified=None``: the action's own claim is not evidence;
- a timeout is ``unknown``, never ``failed``: a goal that timed out may still be
  executing, and calling it failed would invite a replay.

Concurrency: one rclpy node is spun from one thread, so dispatch is serialised
here. Cross-resource overlap is a property of the scheduler (proved with the
fake backend); making the ROS 2 side truly concurrent needs a
MultiThreadedExecutor and belongs to the Phase 3 adapter.
"""

from __future__ import annotations

import threading

from agent_harness.contracts import (
    ActionRequest,
    ExecutionReceipt,
    ExecutionStatus,
    FailureCode,
)
from agent_harness.scripts.ros_tool_executor import (
    SkillCall,
    SkillRegistry,
    ToolCallResult,
    payload_for_call,
    transport_for,
)


class RosBackend:
    def __init__(self, executor, registry: SkillRegistry) -> None:
        self._executor = executor
        self._registry = registry
        self._lock = threading.Lock()

    def execute(self, request: ActionRequest) -> ExecutionReceipt:
        skill, _, action = request.capability.partition(".")
        try:
            schema = self._registry.get_action(skill, action)
            transport, endpoint, ros_type = transport_for(schema)
        except Exception as error:  # noqa: BLE001 - schema problems become receipts
            return self._receipt(
                request,
                ExecutionStatus.REJECTED,
                failure=FailureCode.UNKNOWN_CAPABILITY,
                detail=str(error),
                physical=False,
            )

        payload = payload_for_call(
            SkillCall(index=0, skill=skill, action=action, args=dict(request.arguments))
        )
        record = ToolCallResult(
            step_index=0,
            skill=skill,
            action=action,
            trace_id=request.command_id,
            mode="execute",
            status="pending",
            transport=transport,
            endpoint=endpoint,
            ros_type=ros_type,
            message="Dispatch pending.",
            payload=payload,
        )

        try:
            with self._lock:
                if transport == "action":
                    self._executor._execute_action(record, request.timeout_s)
                elif transport == "service":
                    self._executor._execute_service(record, request.timeout_s)
                elif transport == "topic":
                    self._executor._execute_topic(record)
                else:
                    raise RuntimeError(f"Unsupported transport: {transport}")
        except Exception as error:  # noqa: BLE001 - convert dispatch failures into receipts
            return self._from_exception(request, error)

        return self._from_record(request, record)

    def close(self) -> None:
        """No-op: the script owns the rclpy node and shuts it down itself."""

    # ------------------------------------------------------------------ mapping

    @staticmethod
    def _from_record(request: ActionRequest, record: ToolCallResult) -> ExecutionReceipt:
        payload = {
            "result": record.result,
            "feedback": list(record.feedback),
            "message": record.message,
        }
        if record.status == "succeeded":
            return RosBackend._receipt(
                request, ExecutionStatus.COMPLETED, detail=record.message, payload=payload
            )
        if record.status == "rejected":
            return RosBackend._receipt(
                request,
                ExecutionStatus.REJECTED,
                failure=FailureCode.ACTION_REJECTED,
                detail=record.message,
                physical=False,
                payload=payload,
            )
        return RosBackend._receipt(
            request,
            ExecutionStatus.FAILED,
            failure=FailureCode.ACTION_ABORTED,
            detail=record.message,
            payload=payload,
        )

    @staticmethod
    def _from_exception(request: ActionRequest, error: Exception) -> ExecutionReceipt:
        text = f"{type(error).__name__}: {error}"
        if "Timed out" in str(error):
            # The goal may still be running; the effect is not established.
            return RosBackend._receipt(
                request,
                ExecutionStatus.UNKNOWN,
                failure=FailureCode.EXECUTION_UNCERTAIN,
                detail=text,
            )
        if "unavailable" in str(error):
            return RosBackend._receipt(
                request, ExecutionStatus.FAILED, failure=FailureCode.TRANSPORT_ERROR, detail=text
            )
        if "no field" in str(error) or "Unsupported ROS type" in str(error):
            return RosBackend._receipt(
                request,
                ExecutionStatus.REJECTED,
                failure=FailureCode.INVALID_ARGUMENTS,
                detail=text,
                physical=False,
            )
        return RosBackend._receipt(
            request, ExecutionStatus.FAILED, failure=FailureCode.INTERNAL_ERROR, detail=text
        )

    @staticmethod
    def _receipt(
        request: ActionRequest,
        status: ExecutionStatus,
        *,
        failure: FailureCode | None = None,
        detail: str = "",
        physical: bool | None = None,
        payload: dict | None = None,
    ) -> ExecutionReceipt:
        return ExecutionReceipt(
            episode_id=request.episode_id,
            command_id=request.command_id,
            execution_status=status,
            call_ok=status is ExecutionStatus.COMPLETED,
            physical_effect_verified=physical,
            failure_code=failure,
            detail=detail,
            resource_key=request.resource_key,
            result=payload or {},
        )
