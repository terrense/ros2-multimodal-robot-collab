"""Phase 4: snapshot builder and the model-facing view."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.contracts import ObservationSnapshot, StaleSnapshot
from agent_harness.runtime.snapshots import (
    CalibrationMismatch,
    SnapshotBuilder,
    model_view,
    require_calibration,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def builder(now: float = 100.0) -> SnapshotBuilder:
    return SnapshotBuilder(
        sensor_id="d435",
        calibration_version="calib-2026-11",
        frame_id="map",
        clock=lambda: now,
    )


def test_builder_stamps_identity_and_time():
    snap = builder().build({"pose": [1.0, 2.0]}, state_version=3)
    assert snap.sensor_id == "d435"
    assert snap.calibration_version == "calib-2026-11"
    assert snap.frame_id == "map"
    assert snap.taken_at == 100.0
    assert snap.state_version == 3
    assert snap.snapshot_id.startswith("snap-")


def test_builder_honours_an_explicit_capture_time():
    snap = builder().build({"pose": [0.0, 0.0]}, state_version=1, taken_at=99.5)
    assert snap.taken_at == 99.5


def test_calibration_gate_rejects_an_unknown_calibration():
    snap = builder().build({}, state_version=1)
    require_calibration(snap, "calib-2026-11")
    with pytest.raises(CalibrationMismatch, match="runtime expects"):
        require_calibration(snap, "calib-2025-01")


def test_model_view_strips_evaluator_only_fields():
    snap = builder().build(
        {
            "pose": [1.0, 2.0],
            "_ground_truth_tool": "hex_key",
            "_fault_params": {"drop_at": 0.4},
        },
        state_version=2,
    )
    view = model_view(snap)
    assert view["payload"] == {"pose": [1.0, 2.0]}
    assert view["sensor_id"] == "d435"
    assert view["snapshot_id"] == snap.snapshot_id


def test_model_view_applies_the_freshness_gate():
    snap = builder(now=100.0).build({"pose": [0.0, 0.0]}, state_version=1)
    assert model_view(snap, now_s=100.2, max_age_s=0.5)["payload"] == {"pose": [0.0, 0.0]}
    with pytest.raises(StaleSnapshot):
        model_view(snap, now_s=101.0, max_age_s=0.5)


def test_model_view_without_deadline_does_not_gate():
    snap = builder(now=100.0).build({}, state_version=1)
    assert model_view(snap)["payload"] == {}


def test_hidden_prefix_is_the_only_convention():
    snap = builder().build({"a": 1, "_b": 2, "c_d": 3}, state_version=1)
    payload = model_view(snap)["payload"]
    assert payload == {"a": 1, "c_d": 3}


def test_snapshot_round_trip_preserves_identity():
    snap = builder().build({"pose": [1.0, 2.0]}, state_version=4)
    restored = ObservationSnapshot.from_dict(snap.to_dict())
    assert restored == snap
