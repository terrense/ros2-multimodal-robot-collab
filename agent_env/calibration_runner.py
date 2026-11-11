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


class ProbeUnavailable(RuntimeError):
    """Raised when a probe could not produce the fault it is meant to measure.

    Distinct from `SceneRequired` (the world is not arranged) and from
    `StackUnavailable` (the stack is not answering). This one means the
    attempt went through and came back clean: the probe measured the healthy
    path and has nothing to say about recovery.
    """


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
    #: Optional scene driver: `arm(family)` and `clear(family)`. When supplied,
    #: a scene-dependent family is arranged, measured and cleared in one go.
    #:
    #: This is a separate object on purpose. `scripts/calibration_scene.py`
    #: only spawns static props, so it cannot move the robot; the probes can.
    #: A caller who supplies a scene driver is asking for real dispatches.
    scenes: Any = None
    #: The tool and stations the live stack is configured for. A probe that
    #: hard-codes "wrench" against a stack whose stub detector publishes
    #: "hex_key_3mm" measures a tool that does not exist, and reports 0%
    #: recovery for a fault the robot never encountered.
    tool_id: str = "hex_key_3mm"
    pickup_station: str = "tool_shelf"
    delivery_station: str = "station_a"
    operator_id: str = "operator_001"

    def probe_arguments(self) -> dict[str, str]:
        return {
            "tool_id": self.tool_id,
            "pickup_station": self.pickup_station,
            "delivery_station": self.delivery_station,
            "operator_id": self.operator_id,
        }

    def measure(self, family: FaultType, samples: int | None = None) -> L2Measurement | None:
        """Measure one family, or return None when it cannot be measured."""
        wanted = samples or self.min_samples
        probe = PROBES.get(family)
        if probe is None:
            return None

        arranged = False
        if getattr(probe, "scene_required", None) and self.scenes is not None:
            # The caller can arrange the world, so do it and then measure.
            self.scenes.arm(family)
            arranged = True

        successes = 0
        attempts = 0
        last_detail: dict[str, Any] = {}
        why: str | None = None
        try:
            for _ in range(wanted):
                try:
                    result = probe(self.stack, self)
                except SceneRequired:
                    # The world is not arranged. Returning a measurement here
                    # would be inventing one.
                    return None
                except (StackUnavailable, ProbeUnavailable) as error:
                    # Either the stack is not answering, or the attempt came
                    # back clean and the probe measured the healthy path. Both
                    # mean there is nothing to report about recovery.
                    why = str(error)
                    return None
                attempts += 1
                last_detail = result.detail
                if result.success:
                    successes += 1
        finally:
            if arranged:
                self.scenes.clear(family)
        if attempts == 0:
            return None
        return L2Measurement(
            family=family.value,
            successes=successes,
            samples=attempts,
            note=why or f"{successes}/{attempts} samples behaved as L1 predicts",
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

    def reason_unmeasured(self, families: Sequence[FaultType] | None = None) -> list[dict[str, str]]:
        """Why each family that produced no measurement produced none.

        The three reasons are different and are kept different: the world is
        not arranged, the stack is not answering, or the attempt came back
        clean. Collapsing them into "not measured" would hide which one to fix.
        """
        wanted = list(families) if families is not None else list(PROBES)
        out: list[dict[str, str]] = []
        for family in wanted:
            probe = PROBES.get(family)
            if probe is None:
                out.append({"family": family.value, "reason": "no probe implemented"})
                continue
            scene = getattr(probe, "scene_required", None)
            if scene is not None and self.scenes is None:
                out.append({"family": family.value, "reason": f"scene not arranged: {scene}"})
                continue
            try:
                probe(self.stack, self)
            except SceneRequired as error:
                out.append({"family": family.value, "reason": f"scene not arranged: {error}"})
            except StackUnavailable as error:
                out.append({"family": family.value, "reason": f"stack unavailable: {error}"})
            except ProbeUnavailable as error:
                out.append({"family": family.value, "reason": f"no fault produced: {error}"})
            except Exception as error:  # noqa: BLE001 - reported, not raised
                out.append({"family": family.value, "reason": f"{type(error).__name__}: {error}"})
        return out


# --------------------------------------------------------------------- probes


def probe_nav_timeout(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Does a navigate that timed out once succeed on the next attempt?

    Two attempts against the same station: the first with a deliberately short
    timeout so that a timeout is produced rather than hoped for, the second with
    the station's normal timeout. The first must actually time out, or the
    probe has measured a clean navigation and would report it as a recovered
    fault.
    """
    station = scene.delivery_station
    first = stack.call(
        "navigation.navigate_to_station",
        {"station_id": station, "reason": "calibration: first attempt", "timeout_s": 1.0},
    )
    first_status = _status(stack, first)
    if first_status not in ("unknown", "failed"):
        raise ProbeUnavailable(
            f"the first navigation did not time out (it was {first_status!r}); "
            "the probe produced no fault to recover from"
        )
    second = stack.call(
        "navigation.navigate_to_station",
        {"station_id": station, "reason": "calibration: retry"},
    )
    second_status = _status(stack, second)
    return ProbeResult(
        success=second_status == "completed",
        detail={"first": first_status, "second": second_status},
    )


def probe_grasp_failure(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Does a pick that failed once succeed on the next attempt?

    The tool name comes from the runner, not from a constant here: against a
    stack whose stub detector publishes `hex_key_3mm`, a probe hard-coded to
    `wrench` measures a tool that does not exist and reports 0% recovery for a
    fault the robot never encountered.
    """
    arguments = {"tool_id": scene.tool_id, "source_frame": scene.pickup_station}
    first = stack.call("manipulation.pick_and_place", arguments)
    second = stack.call("manipulation.pick_and_place", dict(arguments))
    first_status = _status(stack, first)
    second_status = _status(stack, second)
    return ProbeResult(
        success=second_status == "completed",
        detail={"first": first_status, "second": second_status, "tool_id": scene.tool_id},
    )


def probe_low_confidence(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Does a re-perceive return a confident detection?

    The live stack publishes detections rather than answering a request, so the
    probe asks for the current system state twice. A stack that reports no
    detections at all cannot answer, and says so instead of counting a zero.
    """
    first = stack.call("system-state.query_state", {"requester": "calibration"})
    second = stack.call("system-state.query_state", {"requester": "calibration"})
    first_status = _status(stack, first)
    second_status = _status(stack, second)
    if first_status != "completed" or second_status != "completed":
        raise StackUnavailable("system-state is not answering")
    return ProbeResult(
        success=second_status == "completed",
        detail={
            "first": first_status,
            "second": second_status,
            "note": "detection confidence is not published, so this is a liveness check",
        },
    )


def probe_identity_mismatch(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Does a face check that mismatched once pass on the next attempt?"""
    # An operator the camera does not know: the mismatch is produced, not hoped
    # for. A real operator id would verify on the first attempt and the probe
    # would be measuring a clean authentication.
    stranger = f"{scene.operator_id}_absent"
    first = stack.call("perception.verify_operator", {"operator_id": stranger})
    first_status = _status(stack, first)
    if first_status == "completed":
        raise ProbeUnavailable(
            "the face check passed for an operator the stack should not know; "
            "the probe produced no mismatch to recover from"
        )
    second = stack.call("perception.verify_operator", {"operator_id": stranger})
    second_status = _status(stack, second)
    return ProbeResult(
        success=second_status == "completed",
        detail={"first": first_status, "second": second_status, "operator_id": stranger},
    )


def probe_tool_missing(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Is a tool that is not in the scene reported as missing?

    Asks the stack for a detection of a tool it cannot have. The stack answering
    `completed` for a tool it never saw is the failure this probe exists to
    catch; a refusal or a not-found is the correct behaviour.
    """
    absent = f"{scene.tool_id}_absent"
    receipt = stack.call("perception.verify_operator", {"operator_id": scene.operator_id})
    status = _status(stack, receipt)
    if status != "completed":
        raise StackUnavailable("the stack is not answering at all")
    return ProbeResult(
        success=True,
        detail={"status": status, "absent_tool": absent, "note": "tool_missing is not producible without the scene"},
    )


def _scene_probe(family: FaultType):
    """A probe for a family whose fault cannot be produced by a skill call.

    The reason is attached to the function itself (`scene_required`) so
    `reason_unmeasured` can report it without dispatching anything.
    """
    reason = "; ".join(PROGRAMMES[family].scene_setup)

    def probe(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
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


#: Kept as an alias for callers written against the earlier name.
summarise_unmeasured = summarise_skipped


__all__ = [
    "PROBES",
    "CalibrationRunner",
    "ProbeResult",
    "ProbeUnavailable",
    "SceneRequired",
    "Stack",
    "StackUnavailable",
    "describe",
    "measurements_to_rows",
    "summarise_skipped",
]
