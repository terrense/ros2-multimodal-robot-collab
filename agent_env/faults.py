"""Fault injection for the delivery task.

Nine fault families from the training plan. Each fault only surfaces as
symptoms in observations (error codes, low confidences, safety events);
the policy never sees the fault type directly.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FaultType(str, Enum):
    VISUAL_OCCLUSION = "visual_occlusion"
    LOW_CONFIDENCE = "low_confidence"
    TOOL_MISSING = "tool_missing"
    NAV_TIMEOUT = "nav_timeout"
    GRASP_FAILURE = "grasp_failure"
    TOOL_DROP = "tool_drop"
    IDENTITY_MISMATCH = "identity_mismatch"
    NO_PROGRESS = "no_progress"
    USER_CANCEL = "user_cancel"


#: Faults where the correct behaviour is to report and abort, not to retry forever.
UNRECOVERABLE = {FaultType.TOOL_MISSING, FaultType.IDENTITY_MISMATCH}

#: Which faults may be combined in double-fault scenarios (avoid contradictory pairs).
COMPATIBLE_PAIRS: list[tuple[FaultType, FaultType]] = [
    (FaultType.VISUAL_OCCLUSION, FaultType.GRASP_FAILURE),
    (FaultType.VISUAL_OCCLUSION, FaultType.NAV_TIMEOUT),
    (FaultType.LOW_CONFIDENCE, FaultType.GRASP_FAILURE),
    (FaultType.LOW_CONFIDENCE, FaultType.NO_PROGRESS),
    (FaultType.NAV_TIMEOUT, FaultType.GRASP_FAILURE),
    (FaultType.NAV_TIMEOUT, FaultType.TOOL_DROP),
    (FaultType.NAV_TIMEOUT, FaultType.NO_PROGRESS),
    (FaultType.GRASP_FAILURE, FaultType.TOOL_DROP),
    (FaultType.GRASP_FAILURE, FaultType.NO_PROGRESS),
    (FaultType.TOOL_DROP, FaultType.NO_PROGRESS),
]


@dataclass
class FaultSpec:
    """One injected fault instance with per-episode parameters."""

    fault: FaultType
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"fault": self.fault.value, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FaultSpec":
        return cls(fault=FaultType(data["fault"]), params=dict(data.get("params") or {}))


def default_params(fault: FaultType, rng: random.Random) -> dict[str, Any]:
    """Randomized per-episode fault parameters (the diversity knobs of the scenario matrix)."""
    if fault == FaultType.VISUAL_OCCLUSION:
        # Detection blocked until the base repositions.
        return {"occluded_confidence": round(rng.uniform(0.0, 0.15), 2)}
    if fault == FaultType.LOW_CONFIDENCE:
        # Re-perceive may help stochastically; reposition clears it deterministically.
        return {
            "degraded_confidence": round(rng.uniform(0.30, 0.62), 2),
            "reperceive_fix_prob": 0.5,
        }
    if fault == FaultType.TOOL_MISSING:
        return {}
    if fault == FaultType.NAV_TIMEOUT:
        # Number of navigate attempts (to the affected station) that time out before one succeeds.
        return {"timeouts_before_success": rng.choice([1, 1, 2])}
    if fault == FaultType.GRASP_FAILURE:
        # Picks fail until a new grasp pose is selected after a fresh detection.
        return {"failed_attempts": rng.choice([1, 1, 2])}
    if fault == FaultType.TOOL_DROP:
        # Tool is dropped mid-transit to the delivery station.
        return {"drop_progress": round(rng.uniform(0.3, 0.8), 2)}
    if fault == FaultType.IDENTITY_MISMATCH:
        # sensor_glitch=True means a single retry passes (recoverable variant).
        return {"sensor_glitch": rng.random() < 0.35}
    if fault == FaultType.NO_PROGRESS:
        # Handover blocked until the operator is called via TTS.
        return {"blocked_handover_attempts": rng.choice([1, 2])}
    if fault == FaultType.USER_CANCEL:
        # Cancel arrives when episode progress first reaches this fraction.
        return {"cancel_at_progress": round(rng.uniform(0.2, 0.85), 2)}
    raise ValueError(f"Unknown fault: {fault}")


class FaultInjector:
    """Samples fault sets for scenario generation."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def none(self) -> list[FaultSpec]:
        return []

    def single(self, fault: FaultType | None = None) -> list[FaultSpec]:
        chosen = fault or self._rng.choice(list(FaultType))
        return [FaultSpec(chosen, default_params(chosen, self._rng))]

    def double(self, pair: tuple[FaultType, FaultType] | None = None) -> list[FaultSpec]:
        first, second = pair or self._rng.choice(COMPATIBLE_PAIRS)
        return [
            FaultSpec(first, default_params(first, self._rng)),
            FaultSpec(second, default_params(second, self._rng)),
        ]

    def sample(self, none_weight: float = 0.3, single_weight: float = 0.5, double_weight: float = 0.2) -> list[FaultSpec]:
        """Samples per the plan's 3:5:2 none/single/double mix by default."""
        roll = self._rng.random()
        if roll < none_weight:
            return self.none()
        if roll < none_weight + single_weight:
            return self.single()
        return self.double()
