"""The model-neutral client every caller uses to submit work.

``ToolClient`` is the only place that knows how a submission becomes a receipt.
It does not know what a prompt is, what a tool schema looks like on the wire, or
which model produced the call 鈥?that is the point.

Rules encoded here:

- an unsupported capability is refused before it reaches a backend;
- a submission returns immediately with a ``queued`` receipt;
- waiting past the deadline while the command is running returns ``unknown``,
  never ``failed`` 鈥?the effect is not established, and a failure would invite
  a retry of something that may already have moved the robot;
- nothing is retried, ever.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import Protocol

from agent_harness.contracts import (
    ActionRequest,
    CapabilityRegistry,
    ExecutionReceipt,
    ExecutionStatus,
    FailureCode,
    UnsupportedCapability,
)

from .policy import ExecutionPolicy, policy_for
from .scheduler import CANCELLED, DONE, RUNNING, Job, ResourceScheduler


class ToolBackend(Protocol):
    """Anything that can turn one request into one receipt."""

    def execute(self, request: ActionRequest) -> ExecutionReceipt: ...

    def close(self) -> None: ...


class ToolClient:
    def __init__(
        self,
        backend: ToolBackend,
        registry: CapabilityRegistry,
        *,
        default_timeout_s: float | None = 60.0,
        max_workers: int = 4,
    ) -> None:
        self._backend = backend
        self._registry = registry
        self._default_timeout_s = default_timeout_s
        self._scheduler = ResourceScheduler(max_workers=max_workers)
        self._lock = threading.Lock()
        self._requests: dict[str, ActionRequest] = {}
        self._policies: dict[str, ExecutionPolicy] = {}
        self._receipts: dict[str, ExecutionReceipt] = {}

    # ------------------------------------------------------------------ submit

    def submit(self, request: ActionRequest) -> ExecutionReceipt:
        try:
            descriptor = self._registry.require(request.capability, "ros2")
        except UnsupportedCapability as error:
            return self._rejected(
                request,
                FailureCode.UNKNOWN_CAPABILITY,
                str(error),
            )

        policy = policy_for(descriptor, default_timeout_s=self._default_timeout_s)
        missing = set(descriptor.required_args) - set(request.arguments)
        if missing:
            return self._rejected(
                request,
                FailureCode.INVALID_ARGUMENTS,
                f"missing arguments: {sorted(missing)}",
            )
        if request.resource_key != policy.resource_key:
            # The descriptor owns the resource, not the caller: an arm command
            # must not be able to declare itself non-exclusive.
            request = replace(request, resource_key=policy.resource_key)
        with self._lock:
            self._requests[request.command_id] = request
            self._policies[request.command_id] = policy

        job = Job(
            command_id=request.command_id,
            resource_key=policy.resource_key,
            fn=lambda: self._execute(request),
        )
        self._scheduler.submit(job)
        return self._receipt(
            request,
            ExecutionStatus.QUEUED,
            resource_key=policy.resource_key,
            detail=f"queued on resource {policy.resource_key}",
        )

    def _execute(self, request: ActionRequest) -> ExecutionReceipt:
        receipt = self._backend.execute(request)
        with self._lock:
            self._receipts[request.command_id] = receipt
        return receipt

    # -------------------------------------------------------------------- wait

    def wait(self, command_id: str, timeout: float | None = None) -> ExecutionReceipt:
        job = self._scheduler.wait(command_id, timeout)
        if job is None:
            raise KeyError(command_id)
        return self._receipt_for_job(job)

    def poll(self, command_id: str) -> ExecutionReceipt:
        job = self._scheduler.job(command_id)
        if job is None:
            raise KeyError(command_id)
        return self._receipt_for_job(job)

    def _receipt_for_job(self, job: Job) -> ExecutionReceipt:
        request = self._request(job.command_id)
        with self._lock:
            finished = self._receipts.get(job.command_id)
        if job.status == DONE:
            if finished is not None:
                return finished
            return self._receipt(
                request,
                ExecutionStatus.FAILED,
                failure=FailureCode.INTERNAL_ERROR,
                detail=job.error or "backend returned no receipt",
                resource_key=job.resource_key,
            )
        if job.status == CANCELLED:
            return self._receipt(
                request,
                ExecutionStatus.REJECTED,
                failure=FailureCode.CANCELLED,
                physical_effect_verified=False,
                detail="cancelled before execution",
                resource_key=job.resource_key,
            )
        if job.status == RUNNING:
            return self._receipt(
                request,
                ExecutionStatus.UNKNOWN,
                failure=FailureCode.EXECUTION_UNCERTAIN,
                detail="wait deadline expired while the command was running",
                resource_key=job.resource_key,
            )
        return self._receipt(
            request,
            ExecutionStatus.QUEUED,
            detail=f"still queued on resource {job.resource_key}",
            resource_key=job.resource_key,
        )

    # ------------------------------------------------------------------ cancel

    def cancel(self, command_id: str) -> bool:
        return self._scheduler.cancel(command_id)

    # ---------------------------------------------------------------- shutdown

    def shutdown(self, drain_timeout: float = 30.0) -> list[str]:
        """Drain the scheduler and close the backend only if nothing survived."""
        unfinished = self._scheduler.drain(drain_timeout)
        if not unfinished:
            self._backend.close()
        return unfinished

    # ---------------------------------------------------------------- helpers

    def _request(self, command_id: str) -> ActionRequest:
        with self._lock:
            return self._requests[command_id]

    def _receipt(
        self,
        request: ActionRequest,
        status: ExecutionStatus,
        *,
        failure: FailureCode | None = None,
        physical_effect_verified: bool | None = None,
        detail: str = "",
        resource_key: str = "episode",
    ) -> ExecutionReceipt:
        return ExecutionReceipt(
            episode_id=request.episode_id,
            command_id=request.command_id,
            execution_status=status,
            call_ok=status is ExecutionStatus.COMPLETED,
            physical_effect_verified=physical_effect_verified,
            failure_code=failure,
            detail=detail,
            resource_key=resource_key,
        )

    def _rejected(
        self,
        request: ActionRequest,
        failure: FailureCode,
        detail: str,
    ) -> ExecutionReceipt:
        return self._receipt(
            request,
            ExecutionStatus.REJECTED,
            failure=failure,
            physical_effect_verified=False,
            detail=detail,
            resource_key="episode",
        )

    @property
    def queue_depth(self) -> dict[str, int]:
        return {
            resource: self._scheduler.queue_depth(resource)
            for resource in ("arm", "base", "episode")
        }

    def is_running(self, resource_key: str) -> bool:
        return self._scheduler.is_running(resource_key)

    @property
    def jobs(self) -> dict[str, ActionRequest]:
        with self._lock:
            return dict(self._requests)
