"""Execution status and failure codes for contract v1.

The central rule: ``unknown`` is a real state, not a polite word for failure.
A command whose effect cannot be established must never be recorded as failed,
because a failure invites a retry and a retry of an unknown-effect command moves
the robot twice.
"""

from __future__ import annotations

from enum import Enum


class ExecutionStatus(str, Enum):
    NOT_STARTED = "not_started"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


# Terminal states: nothing may follow.
TERMINAL = frozenset(
    {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.REJECTED}
)

LEGAL_TRANSITIONS: dict[ExecutionStatus, frozenset[ExecutionStatus]] = {
    ExecutionStatus.NOT_STARTED: frozenset(
        {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING, ExecutionStatus.REJECTED}
    ),
    ExecutionStatus.QUEUED: frozenset(
        {ExecutionStatus.RUNNING, ExecutionStatus.REJECTED, ExecutionStatus.UNKNOWN}
    ),
    ExecutionStatus.RUNNING: frozenset(
        {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.UNKNOWN}
    ),
    # Leaving UNKNOWN requires a reconciliation record: the state is only
    # resolved by reading evidence, never by assuming an outcome.
    ExecutionStatus.UNKNOWN: frozenset(
        {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}
    ),
    ExecutionStatus.COMPLETED: frozenset(),
    ExecutionStatus.FAILED: frozenset(),
    ExecutionStatus.REJECTED: frozenset(),
}


class InvalidTransition(ValueError):
    """Raised when a status change would rewrite physical history."""


def can_transition(
    source: ExecutionStatus,
    target: ExecutionStatus,
    *,
    reconciled_by: str | None = None,
) -> bool:
    """Return whether ``source -> target`` is legal.

    ``reconciled_by`` is the id of the evidence that established the real
    outcome — a receipt id, a snapshot id, or an equivalent record. A bare
    boolean would let anyone resolve ``UNKNOWN`` by assertion; naming the
    evidence makes the resolution auditable after the fact.
    """
    if source is target:
        return True
    if target not in LEGAL_TRANSITIONS[source]:
        return False
    if source is ExecutionStatus.UNKNOWN and not reconciled_by:
        return False
    return True


def require_transition(
    source: ExecutionStatus,
    target: ExecutionStatus,
    *,
    reconciled_by: str | None = None,
) -> None:
    if not can_transition(source, target, reconciled_by=reconciled_by):
        extra = ""
        if source is ExecutionStatus.UNKNOWN and not reconciled_by:
            extra = " (UNKNOWN may only be resolved with the id of the evidence that settled it)"
        raise InvalidTransition(
            f"illegal status transition {source.value} -> {target.value}{extra}"
        )


class FailureCode(str, Enum):
    """Closed set. A receipt that cannot name its failure is ``internal_error``."""

    UNKNOWN_CAPABILITY = "unknown_capability"
    INVALID_ARGUMENTS = "invalid_arguments"
    UNSUPPORTED_UNIT = "unsupported_unit"
    UNSUPPORTED_FRAME = "unsupported_frame"
    PRECONDITION_REJECTED = "precondition_rejected"
    UNSAFE_REQUEST = "unsafe_request"
    TIMEOUT = "timeout"
    TRANSPORT_ERROR = "transport_error"
    ACTION_REJECTED = "action_rejected"
    ACTION_ABORTED = "action_aborted"
    CANCELLED = "cancelled"
    DUPLICATE_COMMAND = "duplicate_command"
    STALE_STATE = "stale_state"
    NOT_RECONCILED = "not_reconciled"
    STOP_TIMEOUT = "stop_timeout"
    EXECUTION_UNCERTAIN = "execution_uncertain"
    INTERNAL_ERROR = "internal_error"


# Receipt states that must name a failure.
_REQUIRES_FAILURE_CODE = frozenset({ExecutionStatus.FAILED, ExecutionStatus.REJECTED})


def requires_failure_code(status: ExecutionStatus) -> bool:
    return status in _REQUIRES_FAILURE_CODE
