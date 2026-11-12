"""Executing the calibration programmes on the live stack.

`l2_calibration.py` states, per fault family, what has to be observed for L1's
model of that family to be believed. This module is the part that goes and
observes it.

Three facts shape the design.

**Some faults cannot be produced by the stack at all.** A visual occlusion
needs a detector that looks at the world; the perception stub publishes a
fixed detection, and an occluder in the scene changes nothing. A tool cannot
be dropped by a stack whose only grasp puts the tool where it was told to put
it. Those probes try anyway, and refuse with the reason they found -- which
is a finding about the stack, not a gap in the operator's afternoon.

**Some families need the world arranged first.** The scene driver places the
props, or makes the detector marginal, and takes them away afterwards. The
driver only ever touches static props and node parameters: it never moves the
robot. The probes do that, and only when a caller asked for real dispatches.

**A probe that cannot run must not become a sample.** Every unmeasured family
stays absent from the measurement list, and the manifest then reports it as
`not_measured`. Fewer than `min_samples` successes never becomes a rate. The
alternative -- filling the gap with a plausible number -- would turn an
assumption back into an unexamined assumption wearing a measurement's clothes.

The stack is a protocol, so the runner is testable without ROS 2: a fake stack
in the tests returns whatever receipts a probe should see.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .faults import FaultType
from .l2_calibration import PROGRAMMES, L2Measurement

#: The confidence below which the mission state machine rejects a detection.
#: It is the mission's own parameter, read here so the probe and the stack
#: agree on what "marginal" means.
DEFAULT_MIN_CONFIDENCE = 0.70

#: The families whose fault has to be arranged in the world before the probe
#: can run. The scene driver places the props or makes the detector marginal;
#: it never moves the robot.
SCENE_FAMILIES = frozenset(
    {
        FaultType.LOW_CONFIDENCE,
        FaultType.VISUAL_OCCLUSION,
        FaultType.TOOL_DROP,
        FaultType.NO_PROGRESS,
    }
)


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

    def read_detections(self, timeout_s: float = 3.0) -> list[dict[str, Any]]:
        """The tool detections the perception nodes published.

        A detection is a *topic*, not a capability: there is no "detect this
        tool" to call. A probe that has to know what the detector saw has to
        read what it published.
        """


def _status(stack: Stack, receipt: Any) -> str:
    return str(stack.receipt_status(receipt))


def _receipt_text(receipt: Any) -> str:
    """Everything a receipt says, as one string.

    The detail is the runtime's own summary; the payload carries the action's
    own result message, nested as deep as the transport put it. A probe that
    reads only the top level sees half of what happened -- and the half it
    misses is usually the half that explains the failure.
    """
    parts: list[str] = []
    detail = getattr(receipt, "detail", None)
    if detail:
        parts.append(str(detail))

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "message" and isinstance(value, str) and value:
                    parts.append(value)
                elif isinstance(value, (dict, list)):
                    collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(getattr(receipt, "result", None))
    return " | ".join(parts)


def _says(receipt: Any, needle: str) -> bool:
    return needle.lower() in _receipt_text(receipt).lower()


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
    #: only spawns static props and sets node parameters, so it cannot move the
    #: robot; the probes can. A caller who supplies a scene driver is asking
    #: for real dispatches.
    scenes: Any = None
    #: The tool and stations the live stack is configured for. A probe that
    #: hard-codes "wrench" against a stack whose stub detector publishes
    #: "hex_key_3mm" measures a tool that does not exist, and reports 0%
    #: recovery for a fault the robot never encountered.
    tool_id: str = "hex_key_3mm"
    pickup_station: str = "tool_shelf"
    delivery_station: str = "station_a"
    operator_id: str = "operator_001"
    #: What the mission state machine counts as a confident detection.
    min_tool_confidence: float = DEFAULT_MIN_CONFIDENCE
    #: How long a motion runs before the cancel is raised. Long enough to be
    #: in flight, short enough that the arm has not finished.
    cancel_delay_s: float = 1.0
    #: How long to wait for a motion that was supposed to be cancelled.
    motion_timeout_s: float = 60.0
    #: How long to let an arm resume take effect before sending a goal.
    resume_delay_s: float = 0.5
    #: Why each family refused to be measured, keyed by family name. The
    #: reason is recorded by the attempt, so it is never asked twice.
    refusals: dict[str, str] = field(default_factory=dict)
    #: The families that produced a measurement.
    measured: set[str] = field(default_factory=set)

    def probe_arguments(self) -> dict[str, str]:
        return {
            "tool_id": self.tool_id,
            "pickup_station": self.pickup_station,
            "delivery_station": self.delivery_station,
            "operator_id": self.operator_id,
        }

    def scene_armed(self, family: FaultType) -> bool:
        """Whether the world for this family has been arranged.

        A probe that says "with the occluder in place" has to know whether the
        occluder is in place.
        """
        return self.scenes is not None

    def measure(self, family: FaultType, samples: int | None = None) -> L2Measurement | None:
        """Measure one family, or return None when it cannot be measured."""
        wanted = samples or self.min_samples
        probe = PROBES.get(family)
        if probe is None:
            self.refusals[family.value] = "no probe implemented"
            return None
        if family in SCENE_FAMILIES and self.scenes is None:
            setup = "; ".join(PROGRAMMES[family].scene_setup)
            self.refusals[family.value] = f"scene not arranged: {setup}"
            return None

        arranged = False
        if family in SCENE_FAMILIES and self.scenes is not None:
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
                except SceneRequired as error:
                    # The world is not arranged. Returning a measurement here
                    # would be inventing one.
                    self.refusals[family.value] = f"scene not arranged: {error}"
                    return None
                except StackUnavailable as error:
                    # The stack is not answering.
                    self.refusals[family.value] = f"stack unavailable: {error}"
                    return None
                except ProbeUnavailable as error:
                    # The attempt came back clean: the probe measured the
                    # healthy path and has nothing to say about recovery.
                    self.refusals[family.value] = f"no fault produced: {error}"
                    return None
                attempts += 1
                last_detail = result.detail
                if result.success:
                    successes += 1
        finally:
            if arranged:
                self.scenes.clear(family)
        if attempts == 0:
            self.refusals.setdefault(family.value, "the probe produced no attempt")
            return None
        self.measured.add(family.value)
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

        A family that was already tried keeps the reason that attempt gave.
        Asking again would dispatch a second time, and the answer would be
        about a different moment than the one that was recorded.
        """
        wanted = list(families) if families is not None else list(PROBES)
        out: list[dict[str, str]] = []
        for family in wanted:
            if family.value in self.measured:
                continue
            if family.value in self.refusals:
                out.append({"family": family.value, "reason": self.refusals[family.value]})
                continue
            probe = PROBES.get(family)
            if probe is None:
                out.append({"family": family.value, "reason": "no probe implemented"})
                continue
            if family in SCENE_FAMILIES and self.scenes is None:
                setup = "; ".join(PROGRAMMES[family].scene_setup)
                out.append({"family": family.value, "reason": f"scene not arranged: {setup}"})
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

    `target_frame` is not optional: the arm server rejects a goal that does not
    name where the tool is going, so a probe that omits it produces a rejection
    rather than a grasp attempt and then reports 0% recovery for a fault that
    was never tried.
    """
    arguments = {
        "tool_id": scene.tool_id,
        "source_frame": scene.pickup_station,
        "target_frame": scene.delivery_station,
    }
    first = stack.call("manipulation.pick_and_place", dict(arguments))
    first_status = _status(stack, first)
    if first_status == "completed":
        raise ProbeUnavailable(
            "the first pick succeeded; the probe produced no grasp failure to recover from"
        )
    if first_status == "rejected":
        raise ProbeUnavailable(
            "the grasp goal was rejected before execution, so no grasp was attempted: "
            f"{getattr(first, 'detail', '')}"
        )
    second = stack.call("manipulation.pick_and_place", dict(arguments))
    second_status = _status(stack, second)
    return ProbeResult(
        success=second_status == "completed",
        detail={
            "first": first_status,
            "second": second_status,
            "tool_id": scene.tool_id,
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

    The mission is asked for a tool the detector does not publish, so the
    fault is produced by the world rather than by the call: there is no
    "detect this tool" capability, and a tool nobody publishes is simply not
    found.

    L1's question is whether the missing tool is reported as missing and is
    *never re-detected*, so the sample needs two attempts: one to see the
    report, one to see that nothing changed.
    """
    missing = f"{scene.tool_id}_absent"
    arguments = {
        "tool_id": missing,
        "target_station": scene.delivery_station,
        "operator_id": scene.operator_id,
    }
    first = stack.call("mission-control.deliver_tool", dict(arguments))
    if not _says(first, "could not find tool"):
        raise ProbeUnavailable(
            "the mission did not report the missing tool as missing: "
            f"{_receipt_text(first) or 'no detail'}"
        )
    second = stack.call("mission-control.deliver_tool", dict(arguments))
    return ProbeResult(
        success=_says(second, "could not find tool"),
        detail={
            "tool_id": missing,
            "first": _receipt_text(first),
            "second": _receipt_text(second),
        },
    )


def probe_low_confidence(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Does a re-perceive of a marginal detection return a confident one?

    The scene is arranged to make the detector marginal. The recovery attempt
    is the stack's own: wait for the next detection of the same tool and look
    at its confidence. Nothing here asks the stack to do anything a detector
    cannot do by itself, so a failure is a statement about the detector.
    """
    reader = getattr(stack, "read_detections", None)
    if reader is None:
        raise StackUnavailable("the stack cannot read the detection topic")
    first = reader(3.0)
    if not first:
        raise StackUnavailable("the stack published no detection")
    marginal = [
        row for row in first if float(row.get("confidence", 1.0)) < scene.min_tool_confidence
    ]
    if not marginal:
        raise ProbeUnavailable(
            "every detection was above the confidence threshold, so no marginal "
            "detection was produced"
        )
    tools = {str(row.get("tool_id")) for row in marginal}
    second = reader(3.0)
    healed = [
        row
        for row in second
        if float(row.get("confidence", 1.0)) >= scene.min_tool_confidence
        and str(row.get("tool_id")) in tools
    ]
    return ProbeResult(
        success=bool(healed),
        detail={
            "first": marginal[0],
            "second": second[:1],
            "threshold": scene.min_tool_confidence,
        },
    )


def probe_visual_occlusion(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Is a blocked detection cleared by changing viewpoint?

    Two things have to be true for this family to mean anything on a live
    stack: the detector has to see the world, and there has to be a way to
    change the viewpoint. The stack has neither -- the perception stub
    publishes a fixed detection, and there is no viewpoint-change capability
    -- so the probe reports that instead of a number.
    """
    reader = getattr(stack, "read_detections", None)
    if reader is None:
        raise StackUnavailable("the stack cannot read the detection topic")
    detections = reader(3.0)
    if not detections:
        raise StackUnavailable("the stack published no detection")
    confidence = float(detections[0].get("confidence", 1.0))
    if confidence >= scene.min_tool_confidence:
        where = "with the occluder in place" if scene.scene_armed(FaultType.VISUAL_OCCLUSION) else ""
        raise ProbeUnavailable(
            f"the detection is still confident {where}: the perception stub "
            "publishes a fixed detection and does not observe the world, so "
            "nothing can occlude it"
        )
    raise ProbeUnavailable(
        "the detection is blocked, but the stack has no viewpoint-change "
        "capability, so there is no way to clear the occlusion"
    )


def probe_tool_drop(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Can a dropped tool be re-picked from where it fell?

    A drop needs something to drop the tool. The stack's only grasp puts the
    tool in the frame it was asked for, and nothing on it releases a held
    tool, so the fault cannot happen and the probe says so.
    """
    arguments = {
        "tool_id": scene.tool_id,
        "source_frame": scene.pickup_station,
        "target_frame": scene.delivery_station,
    }
    receipt = stack.call("manipulation.pick_and_place", dict(arguments))
    status = _status(stack, receipt)
    if status != "completed":
        raise ProbeUnavailable(
            f"the pick did not complete (it was {status!r}), so there was no "
            "tool to drop: "
            f"{_receipt_text(receipt) or 'no detail'}"
        )
    raise ProbeUnavailable(
        "the tool is in the frame it was asked for: nothing on this stack "
        "releases a held tool, so a drop cannot happen"
    )


def probe_no_progress(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Is a blocked handover unblocked by calling the operator?

    The blocker has to make the handover fail. The handover is a simulated
    step in the mission state machine and nothing consults the handover zone,
    so an occupied station changes nothing -- and the probe reports that
    rather than a recovery rate.
    """
    arguments = {
        "tool_id": scene.tool_id,
        "target_station": scene.delivery_station,
        "operator_id": scene.operator_id,
    }
    receipt = stack.call("mission-control.deliver_tool", dict(arguments))
    if _status(stack, receipt) != "completed":
        raise ProbeUnavailable(
            "the mission failed with the handover station occupied: "
            f"{_receipt_text(receipt) or 'no detail'}"
        )
    where = "with the station occupied" if scene.scene_armed(FaultType.NO_PROGRESS) else ""
    raise ProbeUnavailable(
        f"the handover completed {where}: nothing on this stack consults the "
        "handover zone, so it cannot be blocked"
    )


def probe_user_cancel(stack: Stack, scene: CalibrationRunner) -> ProbeResult:
    """Does a cancel stop a motion that is already running?

    The motion runs in a thread and the cancel is raised while it is in
    flight. What is measured is not "a cancel was published" but "the motion
    stopped": a stop that is delivered after the motion has already finished
    has stopped nothing, and counting it as a success would be measuring the
    publish rather than the effect.

    The arm is left running afterwards. A stop that arrives leaves the arm
    server in its stopped state, and the next sample would be measuring a
    rejected goal instead of a motion.
    """
    arguments = {
        "tool_id": scene.tool_id,
        "source_frame": scene.pickup_station,
        "target_frame": scene.delivery_station,
    }
    box: dict[str, Any] = {}

    def move() -> None:
        try:
            box["receipt"] = stack.call("manipulation.pick_and_place", dict(arguments))
        except Exception as error:  # noqa: BLE001 - reported in the detail
            box["error"] = f"{type(error).__name__}: {error}"

    # Clear any stop left behind by an earlier sample before moving.
    stack.call("manipulation.publish_arm_control", _arm_control("arm_start", scene.operator_id))
    # The arm server applies the resume in its own subscription callback, so
    # the goal has to wait for it. Publishing and immediately sending the goal
    # races the subscription against the action request, and the arm rejects a
    # goal that arrives while the stop is still active.
    time.sleep(scene.resume_delay_s)
    worker = threading.Thread(target=move, name="calibration-motion", daemon=True)
    worker.start()
    time.sleep(scene.cancel_delay_s)
    stop = stack.call("manipulation.publish_arm_control", _arm_control("system_stop", scene.operator_id))
    worker.join(timeout=scene.motion_timeout_s)

    receipt = box.get("receipt")
    detail = {
        "stop": _receipt_text(stop),
        "motion": _receipt_text(receipt) if receipt is not None else str(box.get("error", "")),
        "motion_status": _status(stack, receipt) if receipt is not None else "none",
    }
    # Leave the stack usable for the next sample.
    stack.call("manipulation.publish_arm_control", _arm_control("arm_start", scene.operator_id))

    if receipt is None:
        raise ProbeUnavailable(f"the motion never reported back: {detail['motion']}")
    if detail["motion_status"] == "completed":
        return ProbeResult(
            success=False,
            detail=detail | {"note": "the motion finished before the cancel reached it"},
        )
    return ProbeResult(success=_says(receipt, "stopped"), detail=detail)


def _arm_control(command: str, operator_id: str) -> dict[str, Any]:
    """The arguments of an arm control command."""
    return {
        "command": command,
        "operator_id": operator_id,
        "source": "calibration",
        "confidence": 1.0,
        "emergency": command == "system_stop",
        "detail": f"calibration: {command}",
    }


#: The probe for each family.
#:
#: Every family has a probe now. What differs is whether the probe can
#: produce its fault on a given stack:
#:
#:   nav_timeout, identity_mismatch
#:       the fault is produced by the call itself
#:   tool_missing
#:       produced by asking for a tool nobody publishes
#:   low_confidence
#:       needs the detector to be marginal, which the scene driver arranges
#:   user_cancel
#:       produced by raising a cancel while a motion is in flight
#:   grasp_failure
#:       the fault cannot be produced: the simulated arm never fails a grasp
#:   visual_occlusion, tool_drop, no_progress
#:       the fault cannot be produced either, and each probe names the missing
#:       mechanism instead of reporting a rate
#:
#: The distinction matters more than the probes. A probe that measures
#: liveness and reports the result as a recovery rate produces a calibration
#: verdict that looks real and is not: `low_confidence` was exactly that until
#: this list was corrected.
PROBES: dict[FaultType, Any] = {
    FaultType.NAV_TIMEOUT: probe_nav_timeout,
    FaultType.IDENTITY_MISMATCH: probe_identity_mismatch,
    FaultType.TOOL_MISSING: probe_tool_missing,
    FaultType.LOW_CONFIDENCE: probe_low_confidence,
    FaultType.USER_CANCEL: probe_user_cancel,
    # Dispatchable by calling, but the stack cannot be made to fail this way.
    FaultType.GRASP_FAILURE: probe_grasp_failure,
    FaultType.VISUAL_OCCLUSION: probe_visual_occlusion,
    FaultType.TOOL_DROP: probe_tool_drop,
    FaultType.NO_PROGRESS: probe_no_progress,
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
