"""Phase 4 acceptance tests: observation snapshots and capability discovery."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.contracts import (
    CapabilityRegistry,
    ObservationSnapshot,
    StaleSnapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def snapshot(**overrides) -> ObservationSnapshot:
    payload = {
        "snapshot_id": "snap-1",
        "taken_at": 100.0,
        "frame_id": "map",
        "state_version": 7,
        "sensor_id": "d435",
        "calibration_version": "calib-2026-11",
        "units": {"pose": "m"},
        "payload": {"pose": [1.0, 2.0, 0.0]},
    }
    payload.update(overrides)
    return ObservationSnapshot(**payload)


# ---------------------------------------------------------------- P4-R1


def test_snapshot_carries_sensor_and_calibration():
    snap = snapshot()
    assert snap.sensor_id == "d435"
    assert snap.calibration_version == "calib-2026-11"
    assert ObservationSnapshot.from_dict(snap.to_dict()) == snap


def test_snapshot_schema_documents_the_identity_fields():
    from agent_harness.contracts import all_schemas

    properties = all_schemas()["ObservationSnapshot"]["properties"]
    assert "sensor_id" in properties
    assert "calibration_version" in properties


# ---------------------------------------------------------------- P4-R2


def test_fresh_snapshot_passes_the_gate():
    snap = snapshot(taken_at=100.0)
    assert snap.is_fresh(now_s=100.2, max_age_s=0.5)
    snap.require_fresh(now_s=100.2, max_age_s=0.5)


def test_stale_snapshot_is_rejected_before_any_action():
    snap = snapshot(taken_at=100.0)
    assert not snap.is_fresh(now_s=101.0, max_age_s=0.5)
    with pytest.raises(StaleSnapshot, match="exceeds"):
        snap.require_fresh(now_s=101.0, max_age_s=0.5)


def test_snapshot_from_the_future_is_rejected():
    snap = snapshot(taken_at=200.0)
    assert snap.age(now_s=100.0) < 0
    assert not snap.is_fresh(now_s=100.0, max_age_s=10.0)
    with pytest.raises(StaleSnapshot):
        snap.require_fresh(now_s=100.0, max_age_s=10.0)


# ---------------------------------------------------------------- P4-R3


def test_unknown_frame_is_rejected():
    with pytest.raises(ValueError, match="unknown frame"):
        snapshot(frame_id="world")


def test_empty_calibration_identity_is_rejected():
    with pytest.raises(ValueError, match="sensor_id"):
        snapshot(sensor_id="  ")
    with pytest.raises(ValueError, match="calibration_version"):
        snapshot(calibration_version="")


# ---------------------------------------------------------------- P4-R4


def test_capability_description_is_derived_from_the_registry():
    registry = CapabilityRegistry.load(REPO_ROOT)
    lines = registry.describe_for("ros2")
    names = [line.split("(")[0] for line in lines]

    # The model-facing list is the registry's, minus what the schema says the
    # model should not see.
    assert names == [descriptor.name for descriptor in registry.model_visible_for("ros2")]
    deliver = next(line for line in lines if line.startswith("mission-control.deliver_tool("))
    assert "composite" in deliver
    # Non-callable perception internals are not model-visible capabilities.
    joined = " ".join(lines)
    assert "perception.detect_tool_yolov8" not in joined
    assert "perception.recognize_openpose_gesture" not in joined
    # The grasp action is dispatchable by the runtime but not offered to the
    # model: offering it would let the model bypass the mission state machine.
    assert "manipulation.pick_and_place" not in joined
    assert "manipulation.pick_and_place" in registry.names()


def test_capability_description_marks_world_mutation():
    registry = CapabilityRegistry.load(REPO_ROOT)
    lines = registry.describe_for("ros2")
    arm = next(line for line in lines if line.startswith("manipulation.publish_arm_control"))
    assert "mutates=true" in arm
    reading = next(line for line in lines if line.startswith("system-state.query_state"))
    assert "mutates=false" in reading
