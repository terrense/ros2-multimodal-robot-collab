"""Contract v1 — the shared step protocol for both execution paths.

    from agent_harness.contracts import (
        ActionRequest, ExecutionReceipt, TaskOutcome, ExecutionStatus,
        CapabilityRegistry, new_command_id,
    )

Design rules, in order of importance:

1. ``unknown`` is a state, not a hidden failure. A command whose effect cannot
   be established is ``unknown`` and must not be retried blindly.
2. ``call_ok``, ``physical_effect_verified`` and ``TaskOutcome.success`` answer
   three different questions and may disagree.
3. Receipts are evidence; they never become a second authoritative state store.
4. Vocabularies are closed. An unknown unit, frame or failure code is rejected.
"""

from __future__ import annotations

from .adapters import (
    action_from_request,
    execution_status_from_ros_goal_status,
    receipt_for_rejected_goal,
    receipt_from_ros_action_result,
    receipt_from_step_result,
    request_from_agent_env,
)
from .capabilities import CapabilityRegistry, UnsupportedCapability
from .ids import (
    new_command_id,
    new_episode_id,
    new_snapshot_id,
    validate_identifier,
)
from .schema import all_schemas
from .status import (
    ExecutionStatus,
    FailureCode,
    InvalidTransition,
    can_transition,
    require_transition,
)
from .types import (
    CONTRACT_VERSION,
    ActionRequest,
    CapabilityDescriptor,
    ExecutionReceipt,
    ObservationSnapshot,
    StaleSnapshot,
    TaskOutcome,
)
from .vocab import UNIT_TOKENS, known_frames, register_frame

__all__ = [
    "CONTRACT_VERSION",
    "UNIT_TOKENS",
    "ActionRequest",
    "CapabilityDescriptor",
    "CapabilityRegistry",
    "ExecutionReceipt",
    "ExecutionStatus",
    "FailureCode",
    "InvalidTransition",
    "ObservationSnapshot",
    "StaleSnapshot",
    "TaskOutcome",
    "UnsupportedCapability",
    "action_from_request",
    "all_schemas",
    "can_transition",
    "execution_status_from_ros_goal_status",
    "known_frames",
    "new_command_id",
    "new_episode_id",
    "new_snapshot_id",
    "receipt_for_rejected_goal",
    "receipt_from_ros_action_result",
    "receipt_from_step_result",
    "register_frame",
    "request_from_agent_env",
    "require_transition",
    "validate_identifier",
]
