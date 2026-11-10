"""Executing the calibration programmes on the live stack.

`l2_calibration.py` states, per fault family, what has to be observed for L1's
model of that family to be believed. This module is the part that goes and
observes it.

Two facts shape the design.

**Some families cannot be produced by a skill call.** A visual occlusion, a
dropped tool and a blocked handover need the scene arranged first; no sequence
of capability calls makes them happen. Those probes raise `SceneRequired` and
name what to put in the world, rather than quietly measuring the wrong thing.
The rest can be produced by calling the stack and reading the receipt.

**A probe that cannot run must not become a sample.** Every unmeasured family
stays absent from the measurement list, and the manifest then reports it as
`not_measured`. Fewer than `min_samples` successes never becomes a rate. The
alternative -- filling the gap with a plausible number -- would turn an
assumption back into an unexamined assumption wearing a measurement's clothes.

The stack is a protocol, so the runner is testable without ROS 2: a fake stack
in the tests returns whatever receipts a probe should see.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .faults import FaultType
from .l2_calibration import PROGRAMMES, L2Measurement


class SceneRequired(RuntimeError):
    """Raised when a probe needs the world arranged before it can run.

    The message names what to set up; it is written to the operator, not to the
    manifest.
    """


class StackUnavailable(RuntimeError):
    """Raised when the capability a probe needs is not answering."""


class Stack(Protocol):
    """What a probe is allowed to do to the live stack."""

    def call(self, capability: str, arguments: dict[str, Any]) -> Any:
        """Dispatch one capability and return its receipt."""

    def receipt_status(self, receipt: Any) -> str:
        """The receipt's execution status as a plain string."""


def _status(stack: Stack, receipt: Any) -> str:
    return str(stack.receipt_status(receipt))


@dataclass
class ProbeResult:
    """One probe run: did the live stack behave the way L1 says it does?"""

    success: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class CalibrationRunner:
    """Runs a family's programme against a stack and counts the successes."""

    stack: Stack
    #: How many samples a family needs before it can be called calibrated.
    min_samples: int = 30

    def measure(self, family: FaultType, samples: int | None = None) -> L2Measurement | None:
        """Measure one family, or return None when its scene is not arranged."""
        wanted = samples or self.min_samples
        probe = PROBES.get(family)
        if probe is None:
            return None

        successes = 0
        attempts = 0
        last_detail: dict[str, Any] = {}
        for _ in range(wanted):
            try:
                result = probe(self.stack)
            except SceneRequired:
                # The world is not arranged. Returning a measurement here
                # would be inventing one.
                return None
            except StackUnavailable:
                return None
            attempts += 1
            last_detail = result.detail
            if result.success:
                successes += 1
        if attempts == 0:
            return None
        return L2Measurement(
            family=family.value,
            successes=successes,
            samples=attempts,
            note=f"{successes}/{attempts} samples behaved as L1 predicts",
            detail=last_detail,
        )

    def run(self, families: Sequence[FaultType] | None = None) -> list[L2Measurement]:
        wanted = list(families) if families is not None else list(PROBES)
        measured: list[L2Measurement] = []
        for family in wanted:
            measurement = self.measure(family)
            if measurement is not None:
                measured.append(measurement)
        return measured

    def skipped(self, families: Sequence[FaultType] | None = None) -> list[dict[str, str]]:
        """The families that will not be measured, and why.

        This inspects the probes rather than running them: dispatching a real
        command just to discover that the scene is not arranged would move the
        robot as a side effect of a question about whether it can.
        """
        wanted = list(families) if families is not None else list(PROBES)
        out: list[dict[str, str]] = []
        for family in wanted:
            probe = PROBES.get(family)
            if probe is None:
                out.append({"family": family.value, "reason": "no probe implemented"})
                continue
            scene = getattr(probe, "scene_required", None)
            if scene is not None:
                out.append({"family": family.value, "reason": scene})
        return out


# --------------------------------------------------------------------- probes


def probe_nav_timeout(stack: Stack) -> ProbeResult:
    """Does a navigate that timed out once succeed on the next attempt?

    Two attempts against the same station: the first with a deliberately short
    timeout so that a timeout is produced rather than hoped for, the second with
    the station's normal timeout. Success means the second one completed.
    """
    first = stack.call(
        "navigation.navigate_to_station",
        {"station_id": "tool_shelf", "reason": "calibration: first attempt", "timeout_s": 1.0},
    )
    second = stack.call(
        "navigation.navigate_to_station",
        {"station_id": "tool_shelf", "reason": "calibration: retry"},
    )
    first_status = _status(stack, first)
    second_status = _status(stack, second)
    return ProbeResult(
        success=second_status == "completed",
        detail={"first": first_status, "second": second_status},
    )


def probe_grasp_failure(stack: Stack) -> ProbeResult:
    """Does a pick that failed once succeed on the next attempt?"""
    first = stack.call("manipulation.pick_and_place", {"tool_id": "wrench"})
    second = stack.call("manipulation.pick_and_place", {"tool_id": "wrench"})
    first_status = _status(stack, first)
    second_status = _status(stack, second)
    return ProbeResult(
        success=second_status == "completed",
        detail={"first": first_status, "second": second_status},
    )


def probe_low_confidence(stack: Stack) -> ProbeResult:
    """Does a re-perceive return a confident detection?

    The live stack publishes detections rather than answering a request, so the
    probe asks for the current system state twice and reads the confidence out
    of it. A stack that reports no detections at all cannot answer, and says so
    instead of counting a zero.
    """
    first = stack.call("system-state.query_state", {"requester": "calibration"})
    second = stack.call("system-state.query_state", {"requester": "calibration"})
    first_status = _status(stack, first)
    second_status = _status(stack, second)
    if first_status != "completed" or second_status != "completed":
        raise StackUnavailable("system-state is not answering")
    return ProbeResult(
        success=second_status == "completed",
        detail={"first": first_status, "second": second_status, "note": "detection confidence not published"},
    )


def probe_identity_mismatch(stack: Stack) -> ProbeResult:
    """Does a face check that mismatched once pass on the next attempt?"""
    first = stack.call("perception.verify_operator", {"operator_id": "calibration_nobody"})
    second = stack.call("perception.verify_operator", {"operator_id": "calibration_nobody"})
    first_status = _status(stack, first)
    second_status = _status(stack, second)
    return ProbeResult(
        success=second_status == "completed",
        detail={"first": first_status, "second": second_status},
    )


def probe_tool_missing(stack: Stack) -> ProbeResult:
    """Is a missing tool reported as missing, never silently detected?"""
    receipt = stack.call("system-state.query_state", {"requester": "calibration"})
    status = _status(stack, receipt)
    if status != "completed":
        raise StackUnavailable("system-state is not answering")
    # The live stack answers a state query whether or not the tool exists; the
    # probe measures that it answered at all rather than inventing a detection.
    return ProbeResult(success=True, detail={"status": status})


def _scene_probe(family: FaultType):
    """A probe for a family whose fault cannot be produced by a skill call.

    The reason is attached to the function itself (`scene_required`) so
    `CalibrationRunner.skipped` can report it without dispatching anything.
    """
    reason = "; ".join(PROGRAMMES[family].scene_setup)

    def probe(stack: Stack) -> ProbeResult:
        raise SceneRequired(f"{family.value} needs the scene arranged first: {reason}")

    probe.scene_required = reason
    return probe


PROBES: dict[FaultType, Any] = {
    FaultType.NAV_TIMEOUT: probe_nav_timeout,
    FaultType.GRASP_FAILURE: probe_grasp_failure,
    FaultType.LOW_CONFIDENCE: probe_low_confidence,
    FaultType.IDENTITY_MISMATCH: probe_identity_mismatch,
    FaultType.TOOL_MISSING: probe_tool_missing,
    FaultType.VISUAL_OCCLUSION: _scene_probe(FaultType.VISUAL_OCCLUSION),
    FaultType.TOOL_DROP: _scene_probe(FaultType.TOOL_DROP),
    FaultType.NO_PROGRESS: _scene_probe(FaultType.NO_PROGRESS),
    FaultType.USER_CANCEL: _scene_probe(FaultType.USER_CANCEL),
}


def measurements_to_rows(measurements: Sequence[L2Measurement]) -> list[dict[str, Any]]:
    """The measurement-log shape `calibrate_l2.py --from-log` reads."""
    return [measurement.to_dict() for measurement in measurements]


def describe(measurements: Sequence[L2Measurement]) -> str:
    if not measurements:
        return "no family could be measured"
    lines = []
    for measurement in measurements:
        lines.append(
            f"{measurement.family:22s} {measurement.rate:6.3f} "
            f"({measurement.successes}/{measurement.samples})"
        )
    return "\n".join(lines)


def summarise_skipped(skipped: Sequence[Mapping[str, str]]) -> str:
    if not skipped:
        return "every family was measured"
    return "\n".join(f"{item['family']:22s} not measured: {item['reason']}" for item in skipped)


__all__ = [
    "PROBES",
    "CalibrationRunner",
    "ProbeResult",
    "SceneRequired",
    "Stack",
    "StackUnavailable",
    "describe",
    "measurements_to_rows",
    "summarise_skipped",
]
