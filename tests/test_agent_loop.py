"""Phase 5 acceptance tests: the closed loop and its policy backends."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from agent_harness.agent import (
    Budgets,
    ClosedLoop,
    Decide,
    Finish,
    PlanMacroPolicy,
    ScriptedPolicy,
    StubModelPolicy,
)
from agent_harness.contracts import CapabilityRegistry, TaskOutcome
from agent_harness.runtime import Behaviour, FakeBackend, SnapshotBuilder, ToolClient
from agent_harness.runtime.fake_backend import TIMEOUT

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)

ARM = "manipulation.publish_arm_control"
BASE = "navigation.navigate_to_station"
READ = "system-state.query_state"

ARM_ARGS = {"command": "arm_start"}
BASE_ARGS = {"station_id": "station_a", "reason": "pickup"}


class Clock:
    def __init__(self, start: float = 1000.0, step: float = 0.0) -> None:
        self.t = start
        self.step = step

    def __call__(self) -> float:
        value = self.t
        self.t += self.step
        return value


def make_loop(policy, backend=None, *, clock=None, budgets=None, outcome_source=None):
    clock = clock or Clock()
    backend = backend or FakeBackend()
    client = ToolClient(backend, REGISTRY)
    builder = SnapshotBuilder(
        sensor_id="d435",
        calibration_version="calib-test",
        frame_id="map",
        clock=clock,
    )

    def observe():
        return builder.build({"pose": [0.0, 0.0]}, state_version=1)

    loop = ClosedLoop(
        policy=policy,
        client=client,
        registry=REGISTRY,
        observe=observe,
        episode_id="ep-test",
        max_snapshot_age_s=60.0,
        budgets=budgets,
        now=clock,
        outcome_source=outcome_source,
        max_observe_wait_s=0.2,
        observe_interval_s=0.01,
    )
    return loop, client, backend


# ---------------------------------------------------------------- P5-R1


def test_one_action_per_decision_is_enforced():
    policy = ScriptedPolicy([[Decide(ARM, ARM_ARGS)]])  # a list is a policy bug
    loop, client, backend = make_loop(policy)
    try:
        result = loop.run()
        assert result.failure == "invalid_decision"
        assert backend.calls == []
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- P5-R2


def test_unknown_receipt_stops_the_loop_and_is_not_resubmitted():
    backend = FakeBackend(behaviours={ARM: Behaviour(outcome=TIMEOUT)})
    policy = ScriptedPolicy([Decide(ARM, ARM_ARGS), Decide(ARM, ARM_ARGS)])
    loop, client, backend = make_loop(policy, backend)
    try:
        result = loop.run()
        assert result.failure == "unknown_outcome"
        assert result.outcome.success is None
        assert len(result.receipts) == 1
        assert result.receipts[0]["execution_status"] == "unknown"
        assert len(backend.calls) == 1, "an unknown effect must never be resubmitted"
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- P5-R3


def test_a_claim_without_environment_evidence_is_false_success():
    policy = ScriptedPolicy(
        [Decide(ARM, ARM_ARGS), Finish(claimed_success=True)]
    )
    loop, client, _ = make_loop(policy)
    try:
        result = loop.run()
        assert result.failure == "false_success"
        assert result.outcome.success is False
        assert result.claimed_success is True
    finally:
        client.shutdown(1.0)


def test_environment_confirmation_makes_the_claim_true():
    policy = ScriptedPolicy(
        [Decide(ARM, ARM_ARGS), Finish(claimed_success=True)]
    )
    loop, client, _ = make_loop(
        policy, outcome_source=lambda: TaskOutcome(success=True)
    )
    try:
        result = loop.run()
        assert result.failure is None
        assert result.outcome.success is True
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- P5-R4


def test_finishing_before_any_action_is_premature():
    policy = ScriptedPolicy([Finish(claimed_success=True)])
    loop, client, _ = make_loop(policy)
    try:
        result = loop.run()
        assert result.failure == "premature_finish"
        assert result.outcome.success is False
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- P5-R5


def test_turn_budget_stops_the_loop():
    policy = ScriptedPolicy([Decide(ARM, ARM_ARGS), Decide(ARM, ARM_ARGS)])
    loop, client, backend = make_loop(policy, budgets=Budgets(max_turns=1))
    try:
        result = loop.run()
        assert result.failure == "budget_turns"
        assert len(backend.calls) == 1
        assert result.budgets["turns_used"] == 1
    finally:
        client.shutdown(1.0)


def test_token_budget_stops_the_loop():
    policy = StubModelPolicy([Decide(ARM, ARM_ARGS), Decide(ARM, ARM_ARGS)])
    loop, client, _ = make_loop(policy, budgets=Budgets(max_tokens_chars=10))
    try:
        result = loop.run()
        assert result.failure == "budget_tokens"
        assert result.budgets["tokens_used"] >= 10
    finally:
        client.shutdown(1.0)


def test_wall_budget_stops_before_acting():
    policy = ScriptedPolicy([Decide(ARM, ARM_ARGS)])
    clock = Clock(step=0.25)
    loop, client, backend = make_loop(
        policy, clock=clock, budgets=Budgets(max_wall_s=0.2)
    )
    try:
        result = loop.run()
        assert result.failure == "budget_wall"
        assert backend.calls == []
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- P5-R6


def test_cancel_does_not_touch_running_physical_work():
    backend = FakeBackend()
    backend.gate = threading.Event()
    client = ToolClient(backend, REGISTRY)
    builder = SnapshotBuilder(
        sensor_id="d435", calibration_version="calib-test", frame_id="map"
    )
    loop = ClosedLoop(
        policy=ScriptedPolicy([]),
        client=client,
        registry=REGISTRY,
        observe=lambda: builder.build({}, state_version=1),
        episode_id="ep-test",
        max_observe_wait_s=0.1,
    )
    from agent_harness.contracts import ActionRequest, new_command_id

    request = ActionRequest(
        episode_id="ep-test", command_id=new_command_id(),
        capability=ARM, arguments=ARM_ARGS,
    )
    try:
        client.submit(request)
        assert backend.gate_entered.wait(5)
        loop.cancel("operator asked")
        assert client.is_running("arm") is True, "running work must not be relabelled"
        backend.gate.set()
        assert client.wait(request.command_id, timeout=5).execution_status.value == "completed"
    finally:
        if backend.gate is not None:
            backend.gate.set()
        client.shutdown(1.0)


def test_cancelled_loop_reports_cancellation():
    holder: dict = {}

    def responder(view):
        del view
        holder["loop"].cancel("test")
        return Decide(ARM, ARM_ARGS)

    from agent_harness.agent import HumanPolicy

    policy = HumanPolicy(responder)
    loop, client, backend = make_loop(policy)
    holder["loop"] = loop
    try:
        result = loop.run()
        assert result.failure == "cancelled"
        assert backend.calls == []
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- P5-R7


def test_plan_macro_produces_the_same_receipts_as_the_step_loop():
    steps = [(ARM, ARM_ARGS), (BASE, BASE_ARGS)]
    macro_loop, macro_client, _ = make_loop(
        PlanMacroPolicy(steps), outcome_source=lambda: TaskOutcome(success=True)
    )
    step_loop, step_client, _ = make_loop(
        ScriptedPolicy([Decide(ARM, ARM_ARGS), Decide(BASE, BASE_ARGS),
                        Finish(claimed_success=True)]),
        outcome_source=lambda: TaskOutcome(success=True),
    )
    try:
        macro_result = macro_loop.run()
        step_result = step_loop.run()
        assert [r["execution_status"] for r in macro_result.receipts] == [
            "completed", "completed"
        ]
        assert [r["execution_status"] for r in step_result.receipts] == [
            "completed", "completed"
        ]
        assert macro_result.turns == step_result.turns == 2
        assert macro_result.failure is None and step_result.failure is None
    finally:
        macro_client.shutdown(1.0)
        step_client.shutdown(1.0)


def test_plan_macro_stops_when_a_step_fails():
    backend = FakeBackend(behaviours={ARM: Behaviour(outcome="failed")})
    loop, client, _ = make_loop(PlanMacroPolicy([(ARM, ARM_ARGS), (BASE, BASE_ARGS)]), backend)
    try:
        result = loop.run()
        assert result.failure is None
        assert result.outcome.success is None
        assert len(result.receipts) == 1
        assert result.claimed_success is False
    finally:
        client.shutdown(1.0)


# ---------------------------------------------------------------- P5-R8


def test_agent_loop_imports_without_rclpy():
    script = (
        "import sys; sys.modules['rclpy'] = None; "
        "import agent_harness.agent as a; print(a.ClosedLoop.__name__)"
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
    assert "ClosedLoop" in completed.stdout
