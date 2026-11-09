"""ROS 2 backend mapping, exercised with a fake executor and no rclpy.

The backend's only real job is translation: delegate dispatch, then turn what
happened into a contract receipt with honest physical-effect semantics.
"""

from __future__ import annotations

from pathlib import Path

from agent_harness.contracts import (
    ActionRequest,
    ExecutionStatus,
    FailureCode,
    new_command_id,
)
from agent_harness.runtime.ros_backend import RosBackend
from agent_harness.scripts.ros_tool_executor import PlanValidationError

CAPABILITY = "navigation.navigate_to_station"
REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {
    "ros_action": "/skills/navigate_to_station",
    "ros_type": "robot_collab_interfaces/action/NavigateToStation",
    "timeout_sec": 120,
}


class FakeRegistry:
    def __init__(
        self,
        schema: dict | None = None,
        *,
        action: tuple[str, str] = ("navigation", "navigate_to_station"),
    ) -> None:
        self._schema = schema or SCHEMA
        self._action = action

    def get_action(self, skill: str, action: str) -> dict:
        if (skill, action) != self._action:
            raise PlanValidationError(f"Unknown action: {skill}.{action}")
        return self._schema


class FakeExecutor:
    def __init__(self, behaviour) -> None:
        self.behaviour = behaviour
        self.timeouts: list[float | None] = []

    def _execute_action(self, record, timeout_sec=None) -> None:
        self.timeouts.append(timeout_sec)
        self.behaviour(record)

    def _execute_service(self, record, timeout_sec=None) -> None:
        self.timeouts.append(timeout_sec)
        self.behaviour(record)


def request(**overrides) -> ActionRequest:
    payload = {
        "episode_id": "ep-phase2",
        "command_id": new_command_id(),
        "capability": CAPABILITY,
        "arguments": {"station_id": "station_a", "reason": "pickup"},
        "timeout_s": 12.5,
    }
    payload.update(overrides)
    return ActionRequest(**payload)


def succeeded(record) -> None:
    record.status = "succeeded"
    record.message = "Action completed"
    record.result = {"status": 4}


def test_action_success_is_completed_but_not_physically_verified():
    executor = FakeExecutor(succeeded)
    backend = RosBackend(executor, FakeRegistry())

    receipt = backend.execute(request())

    assert receipt.execution_status is ExecutionStatus.COMPLETED
    assert receipt.call_ok is True
    assert receipt.physical_effect_verified is None
    assert receipt.effect_established is False
    assert executor.timeouts == [12.5], "per-command timeout must reach the executor"


def test_rejected_goal_records_no_effect():
    def rejected(record) -> None:
        record.status = "rejected"
        record.message = "Action goal rejected"

    backend = RosBackend(FakeExecutor(rejected), FakeRegistry())
    receipt = backend.execute(request())

    assert receipt.execution_status is ExecutionStatus.REJECTED
    assert receipt.failure_code is FailureCode.ACTION_REJECTED
    assert receipt.physical_effect_verified is False


def test_action_timeout_is_unknown_not_failed():
    def timeout(record) -> None:
        raise RuntimeError("Timed out waiting for action result: /skills/navigate_to_station")

    backend = RosBackend(FakeExecutor(timeout), FakeRegistry())
    receipt = backend.execute(request())

    assert receipt.execution_status is ExecutionStatus.UNKNOWN
    assert receipt.failure_code is FailureCode.EXECUTION_UNCERTAIN
    assert receipt.physical_effect_verified is None


def test_unavailable_server_is_a_transport_failure():
    def unavailable(record) -> None:
        raise RuntimeError("Action server unavailable: /skills/navigate_to_station")

    backend = RosBackend(FakeExecutor(unavailable), FakeRegistry())
    receipt = backend.execute(request())

    assert receipt.execution_status is ExecutionStatus.FAILED
    assert receipt.failure_code is FailureCode.TRANSPORT_ERROR


def test_unknown_capability_never_reaches_dispatch():
    executor = FakeExecutor(succeeded)
    backend = RosBackend(executor, FakeRegistry())

    receipt = backend.execute(request(capability="perception.verify_operator"))

    assert receipt.execution_status is ExecutionStatus.REJECTED
    assert receipt.failure_code is FailureCode.UNKNOWN_CAPABILITY
    assert executor.timeouts == []


def test_service_transport_is_dispatched():
    schema = {"ros_service": "/system/query_state", "ros_type": "pkg/srv/X"}
    executor = FakeExecutor(succeeded)
    backend = RosBackend(
        executor,
        FakeRegistry(schema, action=("system-state", "query_state")),
    )

    receipt = backend.execute(request(capability="system-state.query_state"))

    assert receipt.execution_status is ExecutionStatus.COMPLETED
    assert executor.timeouts == [12.5]


def test_non_terminal_status_is_reported_as_failed():
    def failed(record) -> None:
        record.status = "failed"
        record.message = "Action aborted"

    backend = RosBackend(FakeExecutor(failed), FakeRegistry())
    receipt = backend.execute(request())

    assert receipt.execution_status is ExecutionStatus.FAILED
    assert receipt.failure_code is FailureCode.ACTION_ABORTED


def test_ros_backend_module_imports_without_rclpy():
    import subprocess
    import sys

    script = (
        "import sys; sys.modules['rclpy'] = None; "
        "from agent_harness.runtime.ros_backend import RosBackend; "
        "print(RosBackend.__name__)"
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
    assert "RosBackend" in completed.stdout
