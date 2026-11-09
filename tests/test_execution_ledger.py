"""Phase 3 fault injection: ledger, reconciliation and control semantics.

Every test runs against an in-process fake backend and a SQLite ledger on a
temporary path. Nothing here imports rclpy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.contracts import (
    ActionRequest,
    CapabilityRegistry,
    ExecutionStatus,
    FailureCode,
    new_command_id,
)
from agent_harness.runtime import (
    Behaviour,
    ExecutionLedger,
    FakeBackend,
    LedgerError,
    ToolClient,
)
from agent_harness.runtime.fake_backend import TIMEOUT

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)

ARM = "manipulation.publish_arm_control"
BASE = "navigation.navigate_to_station"
READ = "system-state.query_state"


def make_request(capability: str = ARM, command_id: str | None = None, **kwargs) -> ActionRequest:
    arguments = {
        ARM: {"command": "arm_start"},
        BASE: {"station_id": "station_a", "reason": "pickup"},
        READ: {"requester": "agent_harness"},
    }[capability]
    return ActionRequest(
        episode_id="ep-phase3",
        command_id=command_id or new_command_id(),
        capability=capability,
        arguments=arguments,
        **kwargs,
    )


@pytest.fixture()
def ledger(tmp_path):
    store = ExecutionLedger(tmp_path / "ledger.db")
    yield store
    store.close()


def make_client(backend, ledger, **kwargs) -> ToolClient:
    return ToolClient(backend, REGISTRY, ledger=ledger, **kwargs)


# ------------------------------------------------------------------- fault 1


def test_submission_is_durable_before_dispatch(ledger):
    seen_at_dispatch: list[str] = []

    class CheckingBackend(FakeBackend):
        def execute(self, request):
            record = ledger.get(request.command_id)
            seen_at_dispatch.append(record.status.value if record else "missing")
            return super().execute(request)

    backend = CheckingBackend()
    client = make_client(backend, ledger)
    request = make_request()
    try:
        client.submit(request)
        client.wait(request.command_id, timeout=5)
        assert seen_at_dispatch == ["running"]
        assert ledger.get(request.command_id).status is ExecutionStatus.COMPLETED
    finally:
        client.shutdown(1.0)


# ------------------------------------------------------------------- fault 3


def test_duplicate_command_executes_once(ledger):
    backend = FakeBackend()
    client = make_client(backend, ledger)
    request = make_request()
    try:
        first = client.submit(request)
        assert first.execution_status is ExecutionStatus.QUEUED
        client.wait(request.command_id, timeout=5)

        second = client.submit(request)
        assert second.execution_status is ExecutionStatus.REJECTED
        assert second.failure_code is FailureCode.DUPLICATE_COMMAND
        assert len(backend.calls) == 1
        assert ledger.count() == 1
    finally:
        client.shutdown(1.0)


# --------------------------------------------------------------- fault 2, 4


def test_stale_state_is_refused_before_dispatch(ledger):
    backend = FakeBackend()
    client = make_client(backend, ledger)
    client.observe_state(7)
    try:
        stale = client.submit(make_request(state_version=5))
        assert stale.execution_status is ExecutionStatus.REJECTED
        assert stale.failure_code is FailureCode.STALE_STATE
        assert backend.calls == []

        fresh = client.submit(make_request(state_version=7))
        assert fresh.execution_status is ExecutionStatus.QUEUED
        client.wait(fresh.command_id, timeout=5)
    finally:
        client.shutdown(1.0)


def test_dropped_result_stays_unknown_and_blocks_side_effects(ledger):
    backend = FakeBackend(behaviours={ARM: Behaviour(outcome=TIMEOUT)})
    client = make_client(backend, ledger)
    request = make_request()
    try:
        client.submit(request)
        receipt = client.wait(request.command_id, timeout=5)
        assert receipt.execution_status is ExecutionStatus.UNKNOWN
        assert ledger.get(request.command_id).status is ExecutionStatus.UNKNOWN

        blocked = client.submit(make_request())
        assert blocked.execution_status is ExecutionStatus.REJECTED
        assert blocked.failure_code is FailureCode.NOT_RECONCILED
        assert len(backend.calls) == 1
    finally:
        client.shutdown(1.0)


# ------------------------------------------------------------------- fault 5


def test_restart_marks_orphans_and_blocks_until_acknowledged(tmp_path):
    path = tmp_path / "ledger.db"
    backend = FakeBackend(behaviours={ARM: Behaviour(outcome=TIMEOUT)})
    first = ExecutionLedger(path)
    client = make_client(backend, first)
    request = make_request()
    try:
        client.submit(request)
        client.wait(request.command_id, timeout=5)
    finally:
        client.shutdown(1.0)
        first.close()

    reopened = ExecutionLedger(path)
    try:
        assert reopened.mark_orphans() == 1
        assert [record.command_id for record in reopened.unresolved()] == [request.command_id]

        client2 = make_client(FakeBackend(), reopened)
        try:
            blocked = client2.submit(make_request())
            assert blocked.failure_code is FailureCode.NOT_RECONCILED
        finally:
            client2.shutdown(1.0)

        record = reopened.acknowledge_unknown(
            request.command_id, operator="shen", note="checked robot by hand; no motion observed"
        )
        assert record.status is ExecutionStatus.FAILED
        assert record.reconciled_by == "operator:shen"
        assert reopened.unresolved() == []

        client3 = make_client(FakeBackend(), reopened)
        try:
            allowed = client3.submit(make_request())
            assert allowed.execution_status is ExecutionStatus.QUEUED
            client3.wait(allowed.command_id, timeout=5)
        finally:
            client3.shutdown(1.0)
    finally:
        reopened.close()


def test_acknowledgement_requires_operator_and_note(ledger):
    with pytest.raises(ValueError, match="operator and a note"):
        ledger.acknowledge_unknown("cmd-x", operator="", note="")
    with pytest.raises(ValueError, match="operator and a note"):
        ledger.acknowledge_unknown("cmd-x", operator="shen", note="")
    with pytest.raises(LedgerError):
        ledger.acknowledge_unknown("cmd-missing", operator="shen", note="looked")


# ------------------------------------------------------------------- fault 6


def test_cancel_after_completion_does_not_rewrite_history(ledger):
    backend = FakeBackend()
    client = make_client(backend, ledger)
    request = make_request()
    try:
        client.submit(request)
        assert client.wait(request.command_id, timeout=5).execution_status is ExecutionStatus.COMPLETED

        assert client.cancel(request.command_id) is False
        record = ledger.get(request.command_id)
        assert record.status is ExecutionStatus.COMPLETED
        assert record.failure_code is None
    finally:
        client.shutdown(1.0)


# ------------------------------------------------------------------- fault 7


def test_delayed_receipt_after_terminal_is_recorded_not_applied(ledger):
    from agent_harness.contracts import ExecutionReceipt

    queued = ExecutionReceipt(
        episode_id="ep-1",
        command_id="cmd-late",
        execution_status=ExecutionStatus.QUEUED,
        call_ok=False,
    )
    ledger.record_submitted(
        ActionRequest(
            episode_id="ep-1",
            command_id="cmd-late",
            capability=ARM,
            arguments={"command": "arm_start"},
        ),
        queued,
    )

    first = ExecutionReceipt(
        episode_id="ep-1",
        command_id="cmd-late",
        execution_status=ExecutionStatus.COMPLETED,
        call_ok=True,
        started_at=1.0,
        finished_at=2.0,
    )
    assert ledger.record_finished(first) is True

    late = ExecutionReceipt(
        episode_id="ep-1",
        command_id="cmd-late",
        execution_status=ExecutionStatus.FAILED,
        call_ok=False,
        failure_code=FailureCode.ACTION_ABORTED,
        started_at=1.0,
        finished_at=3.0,
    )
    assert ledger.record_finished(late) is False
    assert ledger.get("cmd-late").status is ExecutionStatus.COMPLETED
    assert any(event["kind"] == "ignored_terminal" for event in ledger.events("cmd-late"))


# ------------------------------------------------------------------- fault 8


def test_controller_failure_is_failed_not_unknown(ledger):
    backend = FakeBackend(
        behaviours={ARM: Behaviour(outcome="failed", failure_code=FailureCode.TRANSPORT_ERROR)}
    )
    client = make_client(backend, ledger)
    request = make_request()
    try:
        client.submit(request)
        receipt = client.wait(request.command_id, timeout=5)
        assert receipt.execution_status is ExecutionStatus.FAILED
        assert receipt.failure_code is FailureCode.TRANSPORT_ERROR
        record = ledger.get(request.command_id)
        assert record.status is ExecutionStatus.FAILED
    finally:
        client.shutdown(1.0)


# ------------------------------------------------------------- fail-closed


def test_fail_closed_blocks_side_effects_and_needs_an_operator_to_clear(ledger):
    backend = FakeBackend()
    client = make_client(backend, ledger)
    try:
        client.set_fail_closed("stop requested")
        blocked = client.submit(make_request())
        assert blocked.execution_status is ExecutionStatus.REJECTED
        assert blocked.failure_code is FailureCode.UNSAFE_REQUEST

        reading = client.submit(make_request(READ))
        assert reading.execution_status is ExecutionStatus.QUEUED, "read-only work stays allowed"
        client.wait(reading.command_id, timeout=5)

        with pytest.raises(ValueError, match="operator and a note"):
            client.clear_fail_closed(operator="shen", note="")

        client.clear_fail_closed(operator="shen", note="stopped by operator, area clear")
        allowed = client.submit(make_request())
        assert allowed.execution_status is ExecutionStatus.QUEUED
        client.wait(allowed.command_id, timeout=5)
    finally:
        client.shutdown(1.0)


# ------------------------------------------------------------ control paths


def test_pause_is_an_operator_arm_command(ledger):
    from agent_harness.runtime import RobotControl

    backend = FakeBackend()
    client = make_client(backend, ledger)
    control = RobotControl(client, episode_id="ep-phase3", operator="shen")
    try:
        receipt = control.pause()
        assert receipt.execution_status is ExecutionStatus.QUEUED
        client.wait(receipt.command_id, timeout=5)
        arguments = client.jobs[receipt.command_id].arguments
        assert arguments["command"] == "arm_pause"
        assert arguments["source"] == "operator_control"
        assert arguments["emergency"] is False
    finally:
        client.shutdown(1.0)


def test_stop_fails_closed_and_only_an_operator_can_resume(ledger):
    from agent_harness.runtime import RobotControl

    backend = FakeBackend()
    client = make_client(backend, ledger)
    control = RobotControl(client, episode_id="ep-phase3", operator="shen")
    try:
        stop = control.stop(reason="area unsafe")
        assert stop.execution_status is ExecutionStatus.QUEUED
        assert client.jobs[stop.command_id].arguments["emergency"] is True
        assert client.fail_closed_reason is not None

        blocked = client.submit(make_request())
        assert blocked.failure_code is FailureCode.UNSAFE_REQUEST

        reading = client.submit(make_request(READ))
        assert reading.execution_status is ExecutionStatus.QUEUED

        control.resume(note="operator checked the cell")
        assert client.fail_closed_reason is None
        allowed = client.submit(make_request())
        assert allowed.execution_status is ExecutionStatus.QUEUED
        client.wait(allowed.command_id, timeout=5)
    finally:
        client.shutdown(1.0)


def test_control_actions_are_recorded_in_the_ledger(ledger):
    from agent_harness.runtime import RobotControl

    backend = FakeBackend()
    client = make_client(backend, ledger)
    control = RobotControl(client, episode_id="ep-phase3", operator="shen")
    try:
        receipt = control.pause()
        client.wait(receipt.command_id, timeout=5)
        assert ledger.get(receipt.command_id).capability == ARM
    finally:
        client.shutdown(1.0)


def test_ack_cli_resolves_an_orphan_without_ros(tmp_path):
    import subprocess
    import sys as _sys

    from agent_harness.contracts import ExecutionReceipt

    path = tmp_path / "ledger.db"
    store = ExecutionLedger(path)
    request = make_request(command_id="cmd-orphan")
    store.record_submitted(
        request,
        ExecutionReceipt(
            episode_id=request.episode_id,
            command_id=request.command_id,
            execution_status=ExecutionStatus.QUEUED,
            call_ok=False,
        ),
    )
    store.mark_orphans()
    store.close()

    script = REPO_ROOT / "agent_harness" / "scripts" / "robot_control.py"
    completed = subprocess.run(
        [
            _sys.executable,
            str(script),
            "ack",
            "--ledger",
            str(path),
            "--operator",
            "shen",
            "--command-id",
            "cmd-orphan",
            "--note",
            "operator checked the cell by hand",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "operator:shen" in completed.stdout

    reopened = ExecutionLedger(path)
    try:
        record = reopened.get("cmd-orphan")
        assert record.status is ExecutionStatus.FAILED
        assert record.reconciled_by == "operator:shen"
    finally:
        reopened.close()


# ------------------------------------------------------------ ledger basics


def test_ledger_survives_reopen(tmp_path):
    path = tmp_path / "ledger.db"
    first = ExecutionLedger(path)
    request = make_request(command_id="cmd-persist")
    from agent_harness.contracts import ExecutionReceipt

    receipt = ExecutionReceipt(
        episode_id=request.episode_id,
        command_id=request.command_id,
        execution_status=ExecutionStatus.QUEUED,
        call_ok=False,
    )
    first.record_submitted(request, receipt)
    first.close()

    second = ExecutionLedger(path)
    try:
        record = second.get("cmd-persist")
        assert record is not None
        assert record.capability == ARM
        assert record.status is ExecutionStatus.QUEUED
    finally:
        second.close()


def test_concurrent_commands_in_different_resources_are_recorded_independently(ledger):
    backend = FakeBackend(default=Behaviour(delay_s=0.05))
    client = make_client(backend, ledger)
    arm = make_request(ARM)
    base = make_request(BASE)
    try:
        client.submit(arm)
        client.submit(base)
        client.wait(arm.command_id, timeout=5)
        client.wait(base.command_id, timeout=5)
        assert ledger.get(arm.command_id).resource_key == "arm"
        assert ledger.get(base.command_id).resource_key == "base"
    finally:
        client.shutdown(1.0)
