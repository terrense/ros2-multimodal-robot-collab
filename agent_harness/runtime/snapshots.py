"""Observation snapshots for the agent runtime (Phase 4).

The builder is deliberately thin and pure: it owns identity, calibration and
frame bookkeeping, not sensor access. A ROS node (or the L1 environment) calls
``build`` with a payload it already has.

``model_view`` is the single place where evaluator-only state is removed from a
snapshot before it reaches a model. The convention: any payload key starting
with ``_`` is evaluator-only (ground truth, fault parameters, hidden scores).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from agent_harness.contracts import (
    ObservationSnapshot,
    new_snapshot_id,
)

HIDDEN_PREFIX = "_"


class CalibrationMismatch(ValueError):
    """Raised when a snapshot was built with a calibration the runtime does not trust."""


class SnapshotBuilder:
    def __init__(
        self,
        *,
        sensor_id: str,
        calibration_version: str,
        frame_id: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.sensor_id = sensor_id
        self.calibration_version = calibration_version
        self.frame_id = frame_id
        self._clock = clock

    def build(
        self,
        payload: dict[str, Any],
        *,
        state_version: int,
        taken_at: float | None = None,
        units: dict[str, str] | None = None,
    ) -> ObservationSnapshot:
        return ObservationSnapshot(
            snapshot_id=new_snapshot_id(),
            taken_at=self._clock() if taken_at is None else taken_at,
            frame_id=self.frame_id,
            state_version=state_version,
            sensor_id=self.sensor_id,
            calibration_version=self.calibration_version,
            units=units or {},
            payload=payload,
        )


def require_calibration(snapshot: ObservationSnapshot, expected: str) -> None:
    if snapshot.calibration_version != expected:
        raise CalibrationMismatch(
            f"snapshot {snapshot.snapshot_id!r} uses calibration "
            f"{snapshot.calibration_version!r}, runtime expects {expected!r}"
        )


def model_view(
    snapshot: ObservationSnapshot,
    *,
    now_s: float | None = None,
    max_age_s: float | None = None,
) -> dict[str, Any]:
    """Render a snapshot for the model.

    - applies the freshness gate when a deadline is given,
    - drops every evaluator-only payload key (leading underscore),
    - keeps the identity fields so a plan can be tied to the exact view.
    """
    if now_s is not None and max_age_s is not None:
        snapshot.require_fresh(now_s, max_age_s)
    data = snapshot.to_dict()
    data["payload"] = {
        key: value
        for key, value in data["payload"].items()
        if not key.startswith(HIDDEN_PREFIX)
    }
    return data
