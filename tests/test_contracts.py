"""Contract v1 acceptance tests (Phase 1, P1-R1..P1-R7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_harness.contracts import (
    CONTRACT_VERSION,
    ActionRequest,
    CapabilityDescriptor,
    CapabilityRegistry,
    ExecutionReceipt,
    ExecutionStatus,
    FailureCode,
    InvalidTransition,
    ObservationSnapshot,
    TaskOutcome,
    UnsupportedCapability,
    action_from_request,
    all_schemas,
    can_transition,
    execution_status_from_ros_goal_status,
    new_command_id,
    receipt_for_rejected_goal,
    receipt_from_ros_action_result,
    receipt_from_step_result,
    request_from_agent_env,
    require_transition,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _request(**overrides) -> ActionRequest:
    payload = {
        "episode_id": "ep-abc123",
        "command_id": "cmd-def456",
        "capability": "navigation.navigate_to_station",
        "arguments": {"station_id": "station_a", "reason": "pickup"},
        "requested_at": 1700000000.0,
        "frame_id": "map",
        "units": {"station_id": "unitless"},
        "resource_key": "base",
        "timeout_s": 120.0,
    }
    payload.update(overrides)
    return ActionRequest(**payload)


def _receipt(**overrides) -> ExecutionReceipt:
    payload = {
        "episode_id": "ep-abc123",
        "command_id": "cmd-def456",
        "execution_status": ExecutionStatus.COMPLETED,
        "call_ok": True,
        "physical_effect_verified": None,
        "started_at": 1700000000.0,
        "finished_at": 1700000005.0,
        "resource_key": "base",
    }
    payload.update(overrides)
    return ExecutionReceipt(**payload)


# --------------------------------------------------------------- P1-R1


def test_action_request_round_trip():
    original = _request()
    restored = ActionRequest.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original


def test_receipt_round_trip():
    original = _receipt(physical_effect_verified=True)
    restored = ExecutionReceipt.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored == original


def test_receipt_backend_ref_round_trip():
    original = _receipt(backend_ref="goal-uuid-1234")
    restored = ExecutionReceipt.from_dict(json.loads(json.dumps(original.to_dict())))
    assert restored.backend_ref == "goal-uuid-1234"


def test_v1_receipt_without_backend_ref_still_parses():
    payload = _receipt().to_dict()
    payload.pop("backend_ref", None)
    receipt = ExecutionReceipt.from_dict(payload)
    assert receipt.backend_ref is None


def test_v1_1_failure_codes_exist():
    for code in (
        FailureCode.DUPLICATE_COMMAND,
        FailureCode.STALE_STATE,
        FailureCode.NOT_RECONCILED,
        FailureCode.STOP_TIMEOUT,
    ):
        assert code.value in {member.value for member in FailureCode}


def test_snapshot_round_trip():
    original = ObservationSnapshot(
        snapshot_id="snap-1",
        taken_at=1700000000.0,
        frame_id="map",
        state_version=7,
        units={"pose": "m"},
        payload={"pose": [1.0, 2.0, 0.0]},
    )
    assert ObservationSnapshot.from_dict(original.to_dict()) == original


def test_outcome_round_trip():
    original = TaskOutcome(success=False, accepted=True, retryable=False,
                           failure_category="grasp_failed")
    assert TaskOutcome.from_dict(original.to_dict()) == original


def test_capability_descriptor_round_trip():
    original = CapabilityDescriptor(
        name="mission-control.deliver_tool",
        granularity="composite",
        available_in=frozenset({"ros2"}),
        required_args=("tool_id", "target_station"),
        mutates_world=True,
        resource_key="episode",
    )
    restored = CapabilityDescriptor.from_dict(original.to_dict())
    assert restored == original
    assert restored.supports("ros2") and not restored.supports("l1")


# --------------------------------------------------------------- P1-R2


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError, match="unknown unit"):
        _request(units={"station_id": "furlong"})


def test_unit_key_must_name_an_argument():
    with pytest.raises(ValueError, match="does not name an argument"):
        _request(units={"tool_id": "m"})


def test_dotted_unit_key_is_rejected():
    with pytest.raises(ValueError, match="top-level name"):
        _request(units={"pose.x": "m"})


def test_velocity_and_force_units_are_available():
    request = _request(
        capability="navigation.navigate_to_station",
        arguments={"station_id": "station_a", "reason": "pickup"},
        units={"station_id": "unitless"},
    )
    assert request.units == {"station_id": "unitless"}

    snapshot = ObservationSnapshot(
        snapshot_id="snap-1",
        taken_at=1.0,
        frame_id="base_link",
        state_version=1,
        units={"speed": "m/s", "yaw_rate": "rad/s"},
        payload={"speed": 0.3, "yaw_rate": 0.1},
    )
    assert snapshot.units["speed"] == "m/s"


def test_unknown_frame_is_rejected():
    with pytest.raises(ValueError, match="unknown frame"):
        _request(frame_id="world")


def test_unknown_failure_code_is_rejected():
    with pytest.raises(ValueError):
        ExecutionReceipt.from_dict(
            {
                **_receipt().to_dict(),
                "execution_status": "failed",
                "call_ok": True,
                "failure_code": "made_up_code",
            }
        )


def test_capability_name_must_be_skill_dot_action():
    with pytest.raises(ValueError, match="skill.action"):
        _request(capability="navigate")


# --------------------------------------------------------------- P1-R3


def test_legal_transitions():
    assert can_transition(ExecutionStatus.NOT_STARTED, ExecutionStatus.QUEUED)
    assert can_transition(ExecutionStatus.QUEUED, ExecutionStatus.RUNNING)
    assert can_transition(ExecutionStatus.RUNNING, ExecutionStatus.COMPLETED)
    assert can_transition(ExecutionStatus.RUNNING, ExecutionStatus.UNKNOWN)


def test_terminal_states_are_terminal():
    for terminal in (
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.REJECTED,
    ):
        for target in ExecutionStatus:
            assert not can_transition(terminal, target) or terminal is target


def test_unknown_requires_reconciliation_to_resolve():
    assert not can_transition(ExecutionStatus.UNKNOWN, ExecutionStatus.COMPLETED)
    assert can_transition(
        ExecutionStatus.UNKNOWN,
        ExecutionStatus.COMPLETED,
        reconciled_by="receipt-9f2c",
    )
    with pytest.raises(InvalidTransition, match="UNKNOWN may only be resolved"):
        require_transition(ExecutionStatus.UNKNOWN, ExecutionStatus.FAILED)


def test_reconciliation_needs_named_evidence_not_a_flag():
    # An empty id is not evidence.
    assert not can_transition(
        ExecutionStatus.UNKNOWN, ExecutionStatus.COMPLETED, reconciled_by=""
    )
    # Evidence does not legalise a transition that is illegal anyway.
    assert not can_transition(
        ExecutionStatus.UNKNOWN, ExecutionStatus.REJECTED, reconciled_by="receipt-1"
    )


def test_receipt_unknown_defaults_to_execution_uncertain():
    receipt = ExecutionReceipt(
        episode_id="ep-1",
        command_id="cmd-1",
        execution_status=ExecutionStatus.UNKNOWN,
        call_ok=False,
        started_at=1.0,
        finished_at=2.0,
    )
    assert receipt.failure_code is FailureCode.EXECUTION_UNCERTAIN
    assert receipt.effect_established is False


def test_receipt_invariants():
    with pytest.raises(ValueError, match="requires a failure_code"):
        _receipt(execution_status=ExecutionStatus.FAILED, call_ok=True)
    with pytest.raises(ValueError, match="cannot report call_ok=True"):
        _receipt(execution_status=ExecutionStatus.QUEUED, call_ok=True)
    with pytest.raises(ValueError, match="requires call_ok=True"):
        _receipt(call_ok=False)
    with pytest.raises(ValueError, match="rejected execution cannot claim"):
        _receipt(
            execution_status=ExecutionStatus.REJECTED,
            call_ok=False,
            failure_code=FailureCode.ACTION_REJECTED,
        )


def test_rejected_receipt_cannot_claim_an_effect_even_with_true():
    with pytest.raises(ValueError, match="rejected execution cannot claim"):
        _receipt(
            execution_status=ExecutionStatus.REJECTED,
            call_ok=False,
            physical_effect_verified=True,
            failure_code=FailureCode.ACTION_REJECTED,
        )


def test_from_dict_cannot_bypass_the_rejected_invariant():
    payload = _receipt(
        execution_status=ExecutionStatus.REJECTED,
        call_ok=False,
        physical_effect_verified=False,
        failure_code=FailureCode.ACTION_REJECTED,
    ).to_dict()
    payload["physical_effect_verified"] = True
    with pytest.raises(ValueError, match="rejected execution cannot claim"):
        ExecutionReceipt.from_dict(payload)


def test_schema_rejects_a_receipt_python_would_reject():
    jsonschema = pytest.importorskip("jsonschema")
    schema = all_schemas()["ExecutionReceipt"]
    payload = _receipt().to_dict()
    payload["execution_status"] = "rejected"
    payload["call_ok"] = False
    payload["failure_code"] = "action_rejected"
    payload["physical_effect_verified"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=payload, schema=schema)


def test_retryable_outcome_cannot_be_accepted():
    with pytest.raises(ValueError, match="retryable"):
        TaskOutcome(success=None, accepted=True, retryable=True)
    assert TaskOutcome(success=None, accepted=False, retryable=True).retryable


# --------------------------------------------------------------- P1-R4


def test_all_l1_actions_survive_the_round_trip():
    from agent_env.protocol import Action

    schema = json.loads(
        (REPO_ROOT / "agent_env" / "schemas" / "agent_skills.json").read_text(
            encoding="utf-8"
        )
    )
    names = sorted(schema["actions"])
    assert len(names) == 13

    for name in names:
        skill, _, action = name.partition(".")
        original = Action(skill=skill, action=action, args={"probe": 1}, reason="r")
        request = request_from_agent_env(original, episode_id="ep-roundtrip")
        assert request.capability == name
        restored = action_from_request(request)
        assert (restored.skill, restored.action) == (skill, action)
        assert restored.args == original.args


# --------------------------------------------------------------- P1-R5 / P1-R6


def test_registry_covers_every_declared_capability():
    registry = CapabilityRegistry.load(REPO_ROOT)

    declared = {}
    for schema_path in sorted((REPO_ROOT / "skills").glob("*/schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        skill = schema["name"]
        for action, spec in (schema.get("actions") or {}).items():
            declared[f"{skill}.{action}"] = bool(spec.get("callable", True))

    callable_ros = {name for name, callable_ in declared.items() if callable_}
    non_callable = set(declared) - callable_ros
    l1_actions = set(
        json.loads(
            (REPO_ROOT / "agent_env" / "schemas" / "agent_skills.json").read_text(
                encoding="utf-8"
            )
        )["actions"]
    )

    # Measured at the pinned revision: 8 declared, 6 agent-callable, 13 L1.
    assert len(declared) == 8
    assert len(callable_ros) == 6
    assert len(l1_actions) == 13
    assert len(callable_ros & l1_actions) == 5

    assert set(registry.names()) == callable_ros | l1_actions
    assert len(registry) == len(callable_ros | l1_actions)

    for name in callable_ros:
        assert registry.require(name, "ros2").name == name
    for name in l1_actions:
        assert registry.require(name, "l1").name == name

    # Declared but not agent-invocable: perception internals, not capabilities.
    assert non_callable == {
        "perception.recognize_openpose_gesture",
        "perception.detect_tool_yolov8",
    }
    for name in non_callable:
        with pytest.raises(UnsupportedCapability):
            registry.get(name)


def test_ros_only_capabilities_are_not_exposed_to_l1():
    registry = CapabilityRegistry.load(REPO_ROOT)
    l1_names = {d.name for d in registry.for_platform("l1")}

    # Exactly one agent-callable capability exists only on the ROS 2 side.
    ros_only = {d.name for d in registry.for_platform("ros2")} - l1_names
    assert ros_only == {"mission-control.deliver_tool"}

    with pytest.raises(UnsupportedCapability):
        registry.require("mission-control.deliver_tool", "l1")


def test_composite_capability_is_marked():
    registry = CapabilityRegistry.load(REPO_ROOT)
    deliver = registry.get("mission-control.deliver_tool")
    assert deliver.granularity == "composite"
    assert registry.get("navigation.navigate_to_station").granularity == "atomic"


# --------------------------------------------------------------- P1-R7


@pytest.mark.parametrize(
    ("schema_name", "instance"),
    [
        ("ActionRequest", _request().to_dict()),
        ("ExecutionReceipt", _receipt().to_dict()),
        (
            "ObservationSnapshot",
            ObservationSnapshot(
                snapshot_id="snap-1",
                taken_at=1.0,
                frame_id="map",
                state_version=1,
                units={"x": "m"},
                payload={"x": 1.0},
            ).to_dict(),
        ),
        ("TaskOutcome", TaskOutcome(success=True).to_dict()),
        (
            "CapabilityDescriptor",
            CapabilityDescriptor(name="hri.publish_tts").to_dict(),
        ),
    ],
)
def test_dataclass_output_satisfies_its_schema(schema_name, instance):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(instance=instance, schema=all_schemas()[schema_name])


def test_schema_documents_are_versioned():
    assert CONTRACT_VERSION == "contract-v1"
    for document in all_schemas().values():
        assert document["x-contract-version"] == CONTRACT_VERSION


# --------------------------------------------------------------- adapters


def test_ros_goal_status_mapping():
    assert execution_status_from_ros_goal_status(4) is ExecutionStatus.COMPLETED
    assert execution_status_from_ros_goal_status(5) is ExecutionStatus.FAILED
    assert execution_status_from_ros_goal_status(6) is ExecutionStatus.FAILED
    assert execution_status_from_ros_goal_status(0) is ExecutionStatus.UNKNOWN
    with pytest.raises(ValueError):
        execution_status_from_ros_goal_status(99)


def test_action_success_is_not_physical_verification():
    receipt = receipt_from_ros_action_result(
        {"status": 4, "result": {"success": True}},
        episode_id="ep-1",
        command_id=new_command_id(),
        started_at=1.0,
        finished_at=2.0,
    )
    assert receipt.execution_status is ExecutionStatus.COMPLETED
    assert receipt.call_ok is True
    assert receipt.physical_effect_verified is None
    assert receipt.effect_established is False


def test_unknown_goal_status_becomes_execution_uncertain():
    receipt = receipt_from_ros_action_result(
        {"status": 0}, episode_id="ep-1", command_id=new_command_id()
    )
    assert receipt.execution_status is ExecutionStatus.UNKNOWN
    assert receipt.failure_code is FailureCode.EXECUTION_UNCERTAIN
    assert receipt.effect_established is False


def test_rejected_goal_records_no_effect():
    receipt = receipt_for_rejected_goal(episode_id="ep-1", command_id=new_command_id())
    assert receipt.execution_status is ExecutionStatus.REJECTED
    assert receipt.physical_effect_verified is False
    assert receipt.call_ok is False


class _StepResult:
    def __init__(self, status: str, error_code: str = "OK", message: str = "") -> None:
        self.status = status
        self.error_code = error_code
        self.message = message


def test_l1_step_result_mapping():
    succeeded = receipt_from_step_result(
        _StepResult("succeeded"), episode_id="ep-1", command_id=new_command_id()
    )
    assert succeeded.execution_status is ExecutionStatus.COMPLETED
    assert succeeded.call_ok is True
    assert succeeded.physical_effect_verified is None

    rejected = receipt_from_step_result(
        _StepResult("rejected", "INVALID_ACTION", "unknown action"),
        episode_id="ep-1",
        command_id=new_command_id(),
    )
    assert rejected.execution_status is ExecutionStatus.REJECTED
    assert rejected.failure_code is FailureCode.INVALID_ARGUMENTS
    assert rejected.physical_effect_verified is False

    failed = receipt_from_step_result(
        _StepResult("failed", "TIMEOUT"), episode_id="ep-1", command_id=new_command_id()
    )
    assert failed.execution_status is ExecutionStatus.FAILED
    assert failed.failure_code is FailureCode.TIMEOUT
