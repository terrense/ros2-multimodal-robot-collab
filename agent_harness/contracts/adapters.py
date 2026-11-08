"""Adapters between contract v1 and the two existing execution paths.

These are translations, not stores. The L1 environment and the ROS 2 nodes stay
authoritative; a receipt produced here is evidence about one submission.

Import rules: ``agent_env`` is imported lazily inside the functions so that
``agent_harness.contracts`` remains importable without the training package on
the path, and ROS 2 types are never imported at all — the ROS adapter works on
plain integers and dicts.
"""

from __future__ import annotations

import time
from typing import Any

from .ids import new_command_id
from .status import ExecutionStatus, FailureCode
from .types import ActionRequest, ExecutionReceipt

# action_msgs/GoalStatus values, kept as plain ints on purpose.
ROS_GOAL_STATUS: dict[int, ExecutionStatus] = {
    0: ExecutionStatus.UNKNOWN,     # STATUS_UNKNOWN
    1: ExecutionStatus.QUEUED,      # STATUS_ACCEPTED
    2: ExecutionStatus.RUNNING,     # STATUS_EXECUTING
    3: ExecutionStatus.RUNNING,     # STATUS_CANCELING
    4: ExecutionStatus.COMPLETED,   # STATUS_SUCCEEDED
    5: ExecutionStatus.FAILED,      # STATUS_CANCELED
    6: ExecutionStatus.FAILED,      # STATUS_ABORTED
}

_L1_ERROR_CODES: dict[str, FailureCode] = {
    "INVALID_ACTION": FailureCode.INVALID_ARGUMENTS,
    "PRECONDITION_REJECTED": FailureCode.PRECONDITION_REJECTED,
    "CONFIRMATION_REQUIRED": FailureCode.PRECONDITION_REJECTED,
    "UNAUTHORIZED": FailureCode.UNSAFE_REQUEST,
    "TIMEOUT": FailureCode.TIMEOUT,
    "CANCELLED": FailureCode.ACTION_ABORTED,
    "GRASP_FAILED": FailureCode.ACTION_ABORTED,
    "NOT_HOLDING": FailureCode.ACTION_ABORTED,
    "NOT_FOUND": FailureCode.PRECONDITION_REJECTED,
    "LOW_CONFIDENCE": FailureCode.PRECONDITION_REJECTED,
    "OPERATOR_ABSENT": FailureCode.PRECONDITION_REJECTED,
}


def request_from_agent_env(
    action: Any,
    *,
    episode_id: str,
    command_id: str | None = None,
    requested_at: float | None = None,
    frame_id: str | None = None,
    units: dict[str, str] | None = None,
    resource_key: str = "episode",
    timeout_s: float | None = None,
) -> ActionRequest:
    """L1 ``agent_env.protocol.Action`` -> contract request."""
    return ActionRequest(
        episode_id=episode_id,
        command_id=command_id or new_command_id(),
        capability=f"{action.skill}.{action.action}",
        arguments=dict(action.args),
        requested_at=time.time() if requested_at is None else requested_at,
        frame_id=frame_id,
        units=units or {},
        resource_key=resource_key,
        timeout_s=timeout_s,
    )


def action_from_request(request: ActionRequest):
    """Contract request -> L1 ``agent_env.protocol.Action`` (drops ids)."""
    from agent_env.protocol import Action

    skill, action = request.capability.split(".", 1)
    return Action(skill=skill, action=action, args=dict(request.arguments))


def receipt_from_step_result(
    step_result: Any,
    *,
    episode_id: str,
    command_id: str,
    started_at: float | None = None,
    finished_at: float | None = None,
    state_version: int | None = None,
    snapshot_id: str | None = None,
    resource_key: str = "episode",
) -> ExecutionReceipt:
    """L1 ``StepResult`` -> contract receipt.

    L1's ``succeeded`` proves the simulator accepted and applied the action. It
    is not physical verification, so ``physical_effect_verified`` stays None.
    """
    status_text = getattr(step_result, "status", "none")
    error_code = str(getattr(step_result, "error_code", "OK"))

    if status_text == "succeeded":
        status = ExecutionStatus.COMPLETED
        call_ok = True
        failure = None
        physical: bool | None = None
    elif status_text == "failed":
        status = ExecutionStatus.FAILED
        call_ok = True
        failure = _L1_ERROR_CODES.get(error_code, FailureCode.ACTION_ABORTED)
        physical = None
    elif status_text == "rejected":
        status = ExecutionStatus.REJECTED
        call_ok = False
        failure = _L1_ERROR_CODES.get(error_code, FailureCode.PRECONDITION_REJECTED)
        physical = False
    else:
        status = ExecutionStatus.NOT_STARTED
        call_ok = False
        failure = None
        physical = False

    started = finished = None
    if status is ExecutionStatus.NOT_STARTED or status is ExecutionStatus.REJECTED:
        pass
    else:
        finished = time.time() if finished_at is None else finished_at
        started = finished if started_at is None else started_at

    return ExecutionReceipt(
        episode_id=episode_id,
        command_id=command_id,
        execution_status=status,
        call_ok=call_ok,
        physical_effect_verified=physical,
        started_at=started,
        finished_at=finished,
        failure_code=failure,
        detail=str(getattr(step_result, "message", "")),
        snapshot_id=snapshot_id,
        state_version=state_version,
        resource_key=resource_key,
    )


def execution_status_from_ros_goal_status(code: int) -> ExecutionStatus:
    if code not in ROS_GOAL_STATUS:
        raise ValueError(f"unknown ROS 2 goal status {code!r}")
    return ROS_GOAL_STATUS[code]


def receipt_from_ros_action_result(
    payload: dict[str, Any],
    *,
    episode_id: str,
    command_id: str,
    started_at: float | None = None,
    finished_at: float | None = None,
    snapshot_id: str | None = None,
    state_version: int | None = None,
    resource_key: str = "episode",
) -> ExecutionReceipt:
    """ROS 2 goal outcome (``{"status": int, "result": {...}}``) -> receipt.

    Critical rule: an action returning SUCCEEDED says nothing about the physical
    world. ``physical_effect_verified`` is therefore always None here; only an
    independent observation may set it.
    """
    code = int(payload.get("status", -1))
    status = execution_status_from_ros_goal_status(code)
    result = payload.get("result") or {}

    if status is ExecutionStatus.COMPLETED:
        call_ok = True
        failure = None
    elif status is ExecutionStatus.FAILED:
        call_ok = True
        failure = FailureCode.ACTION_ABORTED
    elif status is ExecutionStatus.UNKNOWN:
        call_ok = False
        failure = FailureCode.EXECUTION_UNCERTAIN
    else:
        # Accepted / executing / canceling: no verdict yet, and no failure to name.
        call_ok = False
        failure = None

    return ExecutionReceipt(
        episode_id=episode_id,
        command_id=command_id,
        execution_status=status,
        call_ok=call_ok,
        physical_effect_verified=None,
        started_at=started_at,
        finished_at=finished_at,
        failure_code=failure,
        detail=f"ROS 2 goal status {code}",
        snapshot_id=snapshot_id,
        state_version=state_version,
        resource_key=resource_key,
        result=dict(result) if isinstance(result, dict) else {"result": result},
    )


def receipt_for_rejected_goal(
    *,
    episode_id: str,
    command_id: str,
    detail: str = "goal rejected before execution",
    resource_key: str = "episode",
) -> ExecutionReceipt:
    """A goal the server refused: no effect, and provably none."""
    return ExecutionReceipt(
        episode_id=episode_id,
        command_id=command_id,
        execution_status=ExecutionStatus.REJECTED,
        call_ok=False,
        physical_effect_verified=False,
        failure_code=FailureCode.ACTION_REJECTED,
        detail=detail,
        resource_key=resource_key,
    )
