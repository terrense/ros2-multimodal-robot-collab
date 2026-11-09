"""A deterministic backend for tests and dry runs.

It executes nothing and imports no ROS. Every behaviour is declared up front,
and the backend records exactly what happened, so a scheduling test can assert
on order and overlap instead of on timing luck.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from agent_harness.contracts import (
    ActionRequest,
    ExecutionReceipt,
    ExecutionStatus,
    FailureCode,
)

COMPLETED = "completed"
FAILED = "failed"
RAISE = "raise"
TIMEOUT = "timeout"


@dataclass
class Behaviour:
    outcome: str = COMPLETED
    delay_s: float = 0.0
    failure_code: FailureCode = FailureCode.INTERNAL_ERROR
    detail: str = ""


@dataclass
class Call:
    capability: str
    command_id: str
    started_at: float
    finished_at: float | None = None

    @property
    def duration(self) -> float | None:
        if self.finished_at is None:
            return None
        return self.finished_at - self.started_at


class FakeBackend:
    def __init__(
        self,
        behaviours: dict[str, Behaviour] | None = None,
        *,
        default: Behaviour | None = None,
    ) -> None:
        self._behaviours = dict(behaviours or {})
        self._default = default or Behaviour()
        self._lock = threading.Lock()
        self.calls: list[Call] = []
        self.closed = False
        # Set to block every execute() until the test releases it.
        self.gate: threading.Event | None = None
        self.gate_entered = threading.Event()

    def behaviour_for(self, capability: str) -> Behaviour:
        return self._behaviours.get(capability, self._default)

    def execute(self, request: ActionRequest) -> ExecutionReceipt:
        call = Call(
            capability=request.capability,
            command_id=request.command_id,
            started_at=time.monotonic(),
        )
        with self._lock:
            self.calls.append(call)

        if self.gate is not None:
            self.gate_entered.set()
            self.gate.wait()
            self.gate_entered.clear()

        behaviour = self.behaviour_for(request.capability)
        if behaviour.delay_s:
            time.sleep(behaviour.delay_s)

        call.finished_at = time.monotonic()

        if behaviour.outcome == RAISE:
            raise RuntimeError(behaviour.detail or "scripted backend failure")
        if behaviour.outcome == FAILED:
            return self._receipt(
                request,
                ExecutionStatus.FAILED,
                failure_code=behaviour.failure_code,
                detail=behaviour.detail,
            )
        if behaviour.outcome == TIMEOUT:
            return self._receipt(
                request,
                ExecutionStatus.UNKNOWN,
                failure_code=FailureCode.EXECUTION_UNCERTAIN,
                detail=behaviour.detail or "scripted timeout",
            )
        return self._receipt(request, ExecutionStatus.COMPLETED)

    def close(self) -> None:
        self.closed = True

    def command_ids(self) -> list[str]:
        with self._lock:
            return [call.command_id for call in self.calls]

    def capabilities_in_order(self) -> list[str]:
        with self._lock:
            return [call.capability for call in self.calls]

    def intervals(self, capability: str) -> list[tuple[float, float]]:
        with self._lock:
            return [
                (call.started_at, call.finished_at or float("inf"))
                for call in self.calls
                if call.capability == capability
            ]

    @staticmethod
    def _receipt(
        request: ActionRequest,
        status: ExecutionStatus,
        *,
        failure_code: FailureCode | None = None,
        detail: str = "",
    ) -> ExecutionReceipt:
        now = time.time()
        terminal = status in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}
        return ExecutionReceipt(
            episode_id=request.episode_id,
            command_id=request.command_id,
            execution_status=status,
            call_ok=status is ExecutionStatus.COMPLETED,
            physical_effect_verified=None,
            started_at=now if terminal else None,
            finished_at=now if terminal else None,
            failure_code=failure_code,
            detail=detail,
            resource_key=request.resource_key,
        )
