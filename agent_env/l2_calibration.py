"""Calibration of L1's fault behaviour against the live stack.

L1's fault parameters are *configured*, not measured. `default_params` says a
navigation attempt times out `timeouts_before_success` times before one
succeeds, that a low-confidence detection is fixed by a re-perceive with
probability 0.5, that a face mismatch is a transient glitch 35% of the time.
Whether the live stack behaves that way is an empirical question, and until it
is answered the L1 numbers are assumptions.

This module states the question per family, derives the L1 side of it *from
the configuration itself* -- so the reference cannot drift from the code that
produces the scenarios -- and turns recorded L2 measurements into the manifest
the report reads.

The question per family is always the same shape: **when this fault fires,
does the live stack produce the outcome L1 says it produces?** The L2 event
that counts as a success is written down next to it, so "calibrated" never
means "the number was close enough".
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .calibration import MIN_SAMPLES, build_manifest
from .faults import FaultType, default_params

#: How many draws the L1 reference rates are estimated from. Seeded, so the
#: estimate is reproducible.
REFERENCE_DRAWS = 4096


@dataclass(frozen=True)
class L1Reference:
    """What L1 predicts for one family, and where that prediction comes from."""

    family: FaultType
    rate: float
    question: str
    source: str
    draws: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family.value,
            "rate": round(self.rate, 4),
            "question": self.question,
            "source": self.source,
            "draws": self.draws,
        }


@dataclass(frozen=True)
class FamilyProgramme:
    """How to measure one family on the live stack.

    `steps` is the programme an operator (or the live runner) executes;
    `success_event` says what counts as "the live stack behaved as L1 says".
    `scene_setup` lists what has to be arranged in Gazebo first -- the fault
    families that cannot be produced by a skill call alone.
    """

    family: FaultType
    question: str
    success_event: str
    steps: tuple[str, ...]
    scene_setup: tuple[str, ...] = ()
    min_samples: int = MIN_SAMPLES

    @property
    def needs_scene_setup(self) -> bool:
        return bool(self.scene_setup)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family.value,
            "question": self.question,
            "success_event": self.success_event,
            "steps": list(self.steps),
            "scene_setup": list(self.scene_setup),
            "min_samples": self.min_samples,
        }


@dataclass(frozen=True)
class L2Measurement:
    """What the live stack was observed to do, for one family."""

    family: str
    successes: int
    samples: int
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def rate(self) -> float:
        return self.successes / self.samples if self.samples else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "successes": self.successes,
            "samples": self.samples,
            "rate": round(self.rate, 4),
            "note": self.note,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "L2Measurement":
        return cls(
            family=str(data["family"]),
            successes=int(data["successes"]),
            samples=int(data["samples"]),
            note=str(data.get("note", "")),
            detail=dict(data.get("detail") or {}),
        )


# --------------------------------------------------------------------- L1 side


def _sampled_rate(
    family: FaultType,
    *,
    draws: int,
    source: str,
    question: str,
    estimate,
) -> L1Reference:
    """Estimate a family's rate by drawing the configuration itself.

    `estimate` maps one drawn parameter set to the probability that the
    family's stated outcome happens; the rate is their mean. The seed is
    derived from the family name so the estimate does not move between runs.
    """
    import hashlib

    seed = int.from_bytes(hashlib.sha256(family.value.encode("utf-8")).digest()[:4], "big")
    rng = random.Random(seed)
    total = 0.0
    for _ in range(draws):
        total += float(estimate(default_params(family, rng)))
    return L1Reference(
        family=family,
        rate=total / draws,
        question=question,
        source=source,
        draws=draws,
    )


def _deterministic(family: FaultType, *, rate: float, question: str, source: str) -> L1Reference:
    return L1Reference(
        family=family,
        rate=rate,
        question=question,
        source=source,
        draws=1,
    )


def l1_reference_rates(draws: int = REFERENCE_DRAWS) -> dict[FaultType, L1Reference]:
    """What L1 predicts per family, derived from the configuration.

    Nothing here is hand-written: the four stochastic families are estimated by
    drawing `default_params`, and the deterministic ones follow from the
    simulator's own semantics (a reposition clears an occlusion, a TTS call
    unblocks a handover).
    """
    references: dict[FaultType, L1Reference] = {}

    references[FaultType.NAV_TIMEOUT] = _sampled_rate(
        FaultType.NAV_TIMEOUT,
        draws=draws,
        source="default_params.timeouts_before_success == 1",
        question="a navigate that timed out once succeeds on the next attempt",
        estimate=lambda params: 1.0 if int(params.get("timeouts_before_success", 0)) == 1 else 0.0,
    )
    references[FaultType.GRASP_FAILURE] = _sampled_rate(
        FaultType.GRASP_FAILURE,
        draws=draws,
        source="default_params.failed_attempts == 1",
        question="a pick that failed once succeeds on the next attempt",
        estimate=lambda params: 1.0 if int(params.get("failed_attempts", 0)) == 1 else 0.0,
    )
    references[FaultType.LOW_CONFIDENCE] = _sampled_rate(
        FaultType.LOW_CONFIDENCE,
        draws=draws,
        source="default_params.reperceive_fix_prob",
        question="a re-perceive of a low-confidence detection returns a confident one",
        estimate=lambda params: float(params.get("reperceive_fix_prob", 0.5)),
    )
    references[FaultType.IDENTITY_MISMATCH] = _sampled_rate(
        FaultType.IDENTITY_MISMATCH,
        draws=draws,
        source="default_params.sensor_glitch",
        question="a face check that mismatched once passes on the next attempt",
        estimate=lambda params: 1.0 if params.get("sensor_glitch") else 0.0,
    )

    # The simulator's semantics make these deterministic.
    references[FaultType.VISUAL_OCCLUSION] = _deterministic(
        FaultType.VISUAL_OCCLUSION,
        rate=1.0,
        question="a blocked detection is cleared by changing viewpoint",
        source="l1_sim: occlusion_cleared is set by navigation.reposition",
    )
    references[FaultType.TOOL_MISSING] = _deterministic(
        FaultType.TOOL_MISSING,
        rate=1.0,
        question="a missing tool is reported as missing, never re-detected",
        source="l1_sim: _detection_confidence returns None while the fault holds",
    )
    references[FaultType.TOOL_DROP] = _deterministic(
        FaultType.TOOL_DROP,
        rate=1.0,
        question="a dropped tool can be re-picked from where it fell",
        source="l1_sim: drop_pending puts the tool in DROP_ZONE",
    )
    references[FaultType.NO_PROGRESS] = _deterministic(
        FaultType.NO_PROGRESS,
        rate=1.0,
        question="a blocked handover is unblocked by calling the operator",
        source="l1_sim: handover_blocked clears on hri.publish_tts",
    )
    references[FaultType.USER_CANCEL] = _deterministic(
        FaultType.USER_CANCEL,
        rate=1.0,
        question="a cancel stops the motion and terminates the mission",
        source="l1_sim: user_cancel raises a safety event",
    )
    return references


# --------------------------------------------------------------------- L2 side


PROGRAMMES: dict[FaultType, FamilyProgramme] = {
    FaultType.NAV_TIMEOUT: FamilyProgramme(
        family=FaultType.NAV_TIMEOUT,
        question="a navigate that timed out once succeeds on the next attempt",
        success_event="the second navigate to the same station succeeded",
        steps=(
            "navigate_to_station to the far station with a short timeout",
            "on timeout, navigate to the same station again with the normal timeout",
            "count the sample successful when the second attempt succeeded",
        ),
    ),
    FaultType.GRASP_FAILURE: FamilyProgramme(
        family=FaultType.GRASP_FAILURE,
        question="a pick that failed once succeeds on the next attempt",
        success_event="the second pick attempt succeeded",
        steps=(
            "select_grasp_pose, then pick_tool",
            "on failure, select_grasp_pose again, then pick_tool",
            "count the sample successful when the second pick succeeded",
        ),
        scene_setup=("the tool must be reachable but not graspable on the first attempt",),
    ),
    FaultType.LOW_CONFIDENCE: FamilyProgramme(
        family=FaultType.LOW_CONFIDENCE,
        question="a re-perceive of a low-confidence detection returns a confident one",
        success_event="the second request_tool_detection returned a confident detection",
        steps=(
            "request_tool_detection until the confidence drops below the threshold",
            "request_tool_detection again without moving",
            "count the sample successful when the second detection is confident",
        ),
        scene_setup=("the tool must be detectable but marginally lit or far",),
    ),
    FaultType.VISUAL_OCCLUSION: FamilyProgramme(
        family=FaultType.VISUAL_OCCLUSION,
        question="a blocked detection is cleared by changing viewpoint",
        success_event="the detection succeeded after a reposition",
        steps=(
            "request_tool_detection and observe the blocked result",
            "navigation.reposition to viewpoint_left",
            "request_tool_detection again",
            "count the sample successful when the detection succeeded after the move",
        ),
        scene_setup=("an obstacle must occlude the tool from the base's start pose",),
    ),
    FaultType.TOOL_MISSING: FamilyProgramme(
        family=FaultType.TOOL_MISSING,
        question="a missing tool is reported as missing, never re-detected",
        success_event="every detection attempt reported the tool as not found",
        steps=(
            "request_tool_detection for a tool that is not in the scene",
            "repeat twice",
            "count the sample successful when no detection ever came back",
        ),
    ),
    FaultType.IDENTITY_MISMATCH: FamilyProgramme(
        family=FaultType.IDENTITY_MISMATCH,
        question="a face check that mismatched once passes on the next attempt",
        success_event="the second verify_operator succeeded",
        steps=(
            "verify_operator for an operator id the camera does not recognise",
            "verify_operator again with the same id",
            "count the sample successful when the second check passed",
        ),
    ),
    FaultType.TOOL_DROP: FamilyProgramme(
        family=FaultType.TOOL_DROP,
        question="a dropped tool can be re-picked from where it fell",
        success_event="the tool was re-grasped after the drop",
        steps=(
            "pick the tool, then release it mid-transit",
            "navigate to where it fell and pick it again",
            "count the sample successful when the second pick succeeded",
        ),
        scene_setup=("the drop has to happen at a reachable place",),
    ),
    FaultType.NO_PROGRESS: FamilyProgramme(
        family=FaultType.NO_PROGRESS,
        question="a blocked handover is unblocked by calling the operator",
        success_event="the handover succeeded after a TTS call",
        steps=(
            "bring the tool to the handover station",
            "attempt the handover while the station is blocked",
            "publish_tts to call the operator, then hand over again",
            "count the sample successful when the handover succeeded",
        ),
        scene_setup=("the handover station must be occupied by the blocker",),
    ),
    FaultType.USER_CANCEL: FamilyProgramme(
        family=FaultType.USER_CANCEL,
        question="a cancel stops the motion and terminates the mission",
        success_event="the arm reported system_stop and the mission terminated as cancelled",
        steps=(
            "start a motion",
            "raise a user cancel (gesture or topic)",
            "count the sample successful when the motion stopped and the mission was cancelled",
        ),
    ),
}


def plan(families: Iterable[FaultType] | None = None) -> list[dict[str, Any]]:
    """The measurement programme for each family, as data."""
    wanted = list(families) if families is not None else list(PROGRAMMES)
    return [PROGRAMMES[family].to_dict() for family in wanted if family in PROGRAMMES]


# ------------------------------------------------------------------ manifest


def measurements_to_manifest(
    measurements: Iterable[L2Measurement],
    *,
    seed: int,
    references: dict[FaultType, L1Reference] | None = None,
    generated_at: float | None = None,
) -> dict[str, Any]:
    """Build the calibration manifest from recorded L2 measurements.

    A family with no measurement is still listed, with `calibrated: false` and
    the reason "not_measured" -- a family that was never run is not a family
    that happens to match.
    """
    references = references if references is not None else l1_reference_rates()
    by_family = {measurement.family: measurement for measurement in measurements}

    payload: dict[str, dict[str, Any]] = {}
    for family, reference in references.items():
        measurement = by_family.get(family.value)
        if measurement is None:
            payload[family.value] = {
                "l1_rate": reference.rate,
                "l2_successes": 0,
                "l2_samples": 0,
            }
            continue
        payload[family.value] = {
            "l1_rate": reference.rate,
            "l2_successes": measurement.successes,
            "l2_samples": measurement.samples,
        }

    manifest = build_manifest(payload, seed=seed, generated_at=generated_at)
    for family in manifest["families"]:
        name = family["name"]
        if name not in by_family:
            family["calibrated"] = False
            family["reason"] = "not_measured"
        family["question"] = references[FaultType(name)].question
        family["l1_source"] = references[FaultType(name)].source
    manifest["l1_reference_draws"] = REFERENCE_DRAWS
    return manifest


def write_manifest(manifest: dict[str, Any], path: Path | str) -> Path:
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def load_measurements(path: Path | str) -> list[L2Measurement]:
    """Read a measurement log: a JSON list, or {"measurements": [...]}."""
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("measurements", []) if isinstance(data, dict) else data
    return [L2Measurement.from_dict(row) for row in rows]


__all__ = [
    "FamilyProgramme",
    "L1Reference",
    "L2Measurement",
    "PROGRAMMES",
    "REFERENCE_DRAWS",
    "l1_reference_rates",
    "load_measurements",
    "measurements_to_manifest",
    "plan",
    "write_manifest",
]
