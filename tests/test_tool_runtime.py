"""Phase 2 acceptance tests: the runtime, exercised without ROS.

Every test uses the deterministic fake backend; nothing here imports rclpy.
The eight failure paths from the spec are numbered in the test names.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from agent_harness.contracts import (
    ActionRequest,
    CapabilityRegistry,
    ExecutionStatus,
    FailureCode,
    new_command_id,
)
from agent_harness.runtime import Behaviour, FakeBackend, ToolClient
from agent_harness.runtime.fake_backend import RAISE, TIMEOUT

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)

ARM = "manipulation.publish_arm_control"
BASE = "navigation.navigate_to_station"
READ = "system-state.query_state"


def make_request(capability: str, **arguments) -> ActionRequest:
    defaults = {
        ARM: {"command": "arm_start"},
        BASE: {"station_id": "station_a", "reason": "pickup"},
        READ: {"requester": "agent_harness"},
    }
    payload = dict(defaults.get(capability, {}))
    payload.update(arguments)
    return ActionRequest(
        episode_id="ep-phase2",
        command_id=new_command_id(),
        capability=capability,
        arguments=payload,
    )


def make_client(backend: FakeBackend | None = None, **kwargs) -> ToolClient:
    return ToolClient(backend or FakeBackend(), REGISTRY, **kwargs)


# ---------------------------------------------------------------- path 1/2


def test_unknown_capability_is_rejected_before_any_backend_call():
    backend = FakeBackend()
    client = make_client(backend)
    try:
        receipt = client.submit(
            ActionRequest(
                episode_id="ep-1",
                command_id=new_command_id(),
                capability="perception.recognize_openpose_gesture",
            )
        )
        assert receipt.execution_status is ExecutionStatus.REJECTED
        assert receipt.failure_code is FailureCode.UNKNOWN_CAPABILITY
        assert receipt.physical_effect_verified is False
        assert backend.calls == []
    finally:
        client.shutdown(1.0)


def test_missing_arguments_are_rejected_before_any_backend_call():
    backend = FakeBackend()
    client = make_client(backend)
    try:
        request = ActionRequest(
            episode_id="ep-1",
            command_id=new_command_id(),
            capability=ARM,
        )
        receipt = client.submit(request)
        assert receipt.execution_status is ExecutionStatus.REJECTED
        assert receipt.failure_code is FailureCode.INVALID_ARGUMENTS
        assert backend.calls == []
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- path 3/4


def test_same_resource_is_fifo_and_collision_queues():
    backend = FakeBackend(default=Behaviour(delay_s=0.01))
    client = make_client(backend)
    requests = [make_request(ARM) for _ in range(3)]
    try:
        for request in requests:
            receipt = client.submit(request)
            assert receipt.execution_status is ExecutionStatus.QUEUED
        for request in requests:
            final = client.wait(request.command_id, timeout=5)
            assert final.execution_status is ExecutionStatus.COMPLETED
        assert backend.command_ids() == [request.command_id for request in requests]
    finally:
        client.shutdown(1.0)


def test_different_resources_overlap():
    backend = FakeBackend(default=Behaviour(delay_s=0.15))
    client = make_client(backend)
    try:
        arm = make_request(ARM)
        base = make_request(BASE)
        client.submit(arm)
        client.submit(base)
        client.wait(arm.command_id, timeout=5)
        client.wait(base.command_id, timeout=5)

        arm_intervals = backend.intervals(ARM)
        base_intervals = backend.intervals(BASE)
        assert len(arm_intervals) == 1 and len(base_intervals) == 1
        arm_start, arm_end = arm_intervals[0]
        base_start, base_end = base_intervals[0]
        # They must have been in flight at the same time.
        assert base_start < arm_end and arm_start < base_end
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- path 5/6


def test_cancel_only_affects_queued_work():
    backend = FakeBackend()
    backend.gate = threading.Event()
    client = make_client(backend)
    running = make_request(ARM)
    queued = make_request(ARM)
    try:
        client.submit(running)
        assert backend.gate_entered.wait(5), "first command never started"
        client.submit(queued)

        assert client.cancel(running.command_id) is False  # path 6: running is left alone
        assert client.cancel(queued.command_id) is True  # path 5: queued is cancelled

        cancelled = client.wait(queued.command_id, timeout=1)
        assert cancelled.execution_status is ExecutionStatus.REJECTED
        assert cancelled.failure_code is FailureCode.CANCELLED

        backend.gate.set()
        finished = client.wait(running.command_id, timeout=5)
        assert finished.execution_status is ExecutionStatus.COMPLETED
        assert backend.command_ids() == [running.command_id]
    finally:
        if backend.gate is not None:
            backend.gate.set()
        client.shutdown(1.0)


# ---------------------------------------------------------------- path 7


def test_timeout_is_unknown_not_failed():
    backend = FakeBackend(behaviours={ARM: Behaviour(delay_s=0.3)})
    client = make_client(backend)
    request = make_request(ARM)
    try:
        client.submit(request)
        provisional = client.wait(request.command_id, timeout=0.05)
        assert provisional.execution_status is ExecutionStatus.UNKNOWN
        assert provisional.failure_code is FailureCode.EXECUTION_UNCERTAIN
        assert provisional.physical_effect_verified is None

        # The effect is established later by the real receipt, not assumed.
        resolved = client.wait(request.command_id, timeout=5)
        assert resolved.execution_status is ExecutionStatus.COMPLETED
    finally:
        client.shutdown(1.0)


def test_backend_scripted_timeout_maps_to_unknown():
    backend = FakeBackend(behaviours={ARM: Behaviour(outcome=TIMEOUT)})
    client = make_client(backend)
    request = make_request(ARM)
    try:
        client.submit(request)
        receipt = client.wait(request.command_id, timeout=5)
        assert receipt.execution_status is ExecutionStatus.UNKNOWN
        assert receipt.failure_code is FailureCode.EXECUTION_UNCERTAIN
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- path 8


def test_backend_exception_becomes_internal_error_and_client_survives():
    backend = FakeBackend(behaviours={ARM: Behaviour(outcome=RAISE, detail="boom")})
    client = make_client(backend)
    failing = make_request(ARM)
    healthy = make_request(READ)
    try:
        client.submit(failing)
        receipt = client.wait(failing.command_id, timeout=5)
        assert receipt.execution_status is ExecutionStatus.FAILED
        assert receipt.failure_code is FailureCode.INTERNAL_ERROR
        assert "boom" in receipt.detail

        client.submit(healthy)
        assert client.wait(healthy.command_id, timeout=5).execution_status is ExecutionStatus.COMPLETED
    finally:
        client.shutdown(1.0)


def test_shutdown_reports_in_flight_work_and_does_not_close_the_backend():
    backend = FakeBackend()
    backend.gate = threading.Event()
    client = make_client(backend)
    running = make_request(ARM)
    queued = make_request(ARM)
    try:
        client.submit(running)
        assert backend.gate_entered.wait(5)
        client.submit(queued)

        unfinished = client.shutdown(drain_timeout=0.05)
        assert unfinished == [running.command_id]
        assert backend.closed is False, "backend must stay open while work is in flight"

        backend.gate.set()
        assert client.wait(running.command_id, timeout=5).execution_status is ExecutionStatus.COMPLETED
        assert client.wait(queued.command_id, timeout=1).failure_code is FailureCode.CANCELLED

        assert client.shutdown(drain_timeout=1.0) == []
        assert backend.closed is True
    finally:
        if backend.gate is not None:
            backend.gate.set()


# ------------------------------------------------------------ import hygiene


def test_runtime_imports_without_rclpy():
    script = (
        "import sys; sys.modules['rclpy'] = None; "
        "import agent_harness.runtime as r; "
        "print(r.ToolClient.__name__)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "ToolClient" in completed.stdout


@pytest.mark.parametrize("capability", [ARM, BASE, READ])
def test_receipts_carry_identity_and_resource(capability):
    backend = FakeBackend()
    client = make_client(backend)
    request = make_request(capability)
    try:
        client.submit(request)
        receipt = client.wait(request.command_id, timeout=5)
        assert receipt.command_id == request.command_id
        assert receipt.episode_id == request.episode_id
        assert receipt.execution_status is ExecutionStatus.COMPLETED
        assert receipt.call_ok is True
        assert receipt.physical_effect_verified is None
    finally:
        client.shutdown(1.0)
