"""L1 text-level fast simulator for the tool-delivery task.

Millisecond-per-step stand-in for Gazebo used for bulk data generation and
PPO rollouts. World logic mirrors mission_state_machine.py; fault symptoms
mirror what the L2 Gazebo adapter reports. Pure stdlib.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .faults import UNRECOVERABLE, FaultSpec, FaultType
from .protocol import (
    MAX_EPISODE_STEPS,
    MIN_TOOL_CONFIDENCE,
    PHASE_PROGRESS,
    Action,
    ActionRegistry,
    Detection,
    ErrorCode,
    Observation,
    Outcome,
    Phase,
    StepResult,
)

TOOLS = ["hex_key", "screwdriver", "wrench", "allen_key", "torque_meter"]
PICKUP_STATION = "tool_shelf"
WORKSTATIONS = ["station_a", "station_b"]
ALL_STATIONS = [PICKUP_STATION, *WORKSTATIONS, "handover_zone", "base_dock"]
DROP_ZONE = "drop_zone"  # pseudo-station that appears only after a mid-transit drop

#: Actions still allowed once the user has cancelled the mission.
CANCEL_SAFE_ACTIONS = {
    "manipulation.publish_arm_control",
    "hri.publish_tts",
    "system-state.query_state",
    "manipulation.verify_grasp",
    "mission.terminate",
}
MOTION_ACTIONS = {
    "navigation.navigate_to_station",
    "navigation.reposition",
    "manipulation.pick_tool",
    "manipulation.handover_tool",
}


@dataclass
class Scenario:
    tool_id: str
    target_station: str
    operator_id: str
    faults: list[FaultSpec] = field(default_factory=list)
    require_confirmation: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "target_station": self.target_station,
            "operator_id": self.operator_id,
            "faults": [f.to_dict() for f in self.faults],
            "require_confirmation": self.require_confirmation,
        }

    @classmethod
    def sample(cls, rng: random.Random, faults: list[FaultSpec]) -> "Scenario":
        return cls(
            tool_id=rng.choice(TOOLS),
            target_station=rng.choice(WORKSTATIONS),
            operator_id=f"operator_{rng.randint(1, 5):03d}",
            faults=faults,
            require_confirmation=rng.random() < 0.7,
        )


class L1DeliveryEnv:
    """Closed-loop env: reset() -> Observation, step(Action) -> (Observation, reward_components, done, info)."""

    def __init__(self, scenario: Scenario, seed: int = 0, registry: ActionRegistry | None = None) -> None:
        self.scenario = scenario
        self.rng = random.Random(seed)
        self.registry = registry or ActionRegistry.load()
        self._fault_params: dict[FaultType, dict[str, Any]] = {
            spec.fault: dict(spec.params) for spec in scenario.faults
        }
        self.reset_state()

    # ------------------------------------------------------------------ state

    def reset_state(self) -> None:
        self.step_index = 0
        self.robot_location = "base_dock"
        self.tool_location = PICKUP_STATION
        self.holding: str | None = None
        self.grasp_verified = False
        self.delivered = False
        self.operator_verified = False
        self.auth_attempts = 0
        self.auth_locked = False
        self.fresh_detection: Detection | None = None
        self.valid_grasp_id: str | None = None
        self.grasp_counter = 0
        self.pick_failures = 0
        self.failed_detect_attempts = 0
        self.repositions = 0
        self.occlusion_cleared = False
        self.low_conf_cleared = False
        self.nav_timeouts_left = int(self._fault_params.get(FaultType.NAV_TIMEOUT, {}).get("timeouts_before_success", 0))
        self.grasp_failures_left = int(self._fault_params.get(FaultType.GRASP_FAILURE, {}).get("failed_attempts", 0))
        self.drop_pending = FaultType.TOOL_DROP in self._fault_params
        self.dropped = False
        self.handover_blocked = FaultType.NO_PROGRESS in self._fault_params
        self.operator_called = False
        self.confirmation_granted = False
        self.cancel_active = False
        self.motion_stopped = False
        self.safety_events: list[str] = []
        self.no_progress_count = 0
        self.grounding_violations = 0
        self.safety_violations = 0
        self.outcome = Outcome.RUNNING
        self.last_action: Action | None = None
        self.last_result = StepResult()
        self._prev_signature = self._signature()
        self._prev_progress = self.progress()

    def _signature(self) -> tuple:
        return (
            self.robot_location,
            self.tool_location,
            self.holding,
            self.operator_verified,
            self.grasp_verified,
            self.delivered,
            self.confirmation_granted,
            self.fresh_detection.confidence if self.fresh_detection else None,
            self.valid_grasp_id,
            self.operator_called,
            self.motion_stopped,
        )

    # -------------------------------------------------------------- progress

    def phase(self) -> Phase:
        if self.outcome not in (Outcome.RUNNING,):
            return Phase.DONE
        if self.delivered:
            return Phase.DONE
        if not self.operator_verified:
            return Phase.VERIFY_IDENTITY
        if self.holding is None:
            if self.fresh_detection is None and not self.dropped:
                if self.robot_location != self.tool_location:
                    return Phase.DETECT_TOOL
                return Phase.DETECT_TOOL
            if self.robot_location != self.tool_location:
                return Phase.NAVIGATE_PICKUP
            return Phase.PICK_TOOL
        if not self.grasp_verified:
            return Phase.VERIFY_GRASP
        if self.robot_location != self.scenario.target_station:
            return Phase.NAVIGATE_DELIVER
        return Phase.HANDOVER

    def progress(self) -> float:
        return PHASE_PROGRESS[self.phase()]

    # ----------------------------------------------------------------- reset

    def reset(self) -> Observation:
        self.reset_state()
        return self._observation()

    def _observation(self) -> Observation:
        detections = [self.fresh_detection] if self.fresh_detection else []
        return Observation(
            step_index=self.step_index,
            task={
                "tool_id": self.scenario.tool_id,
                "target_station": self.scenario.target_station,
                "operator_id": self.scenario.operator_id,
                "require_confirmation": self.scenario.require_confirmation,
            },
            robot_location=self.robot_location,
            holding_tool=self.holding,
            operator_verified=self.operator_verified,
            detections=list(detections),
            last_action=self.last_action.to_dict() if self.last_action else None,
            last_result=self.last_result,
            safety_events=list(self.safety_events),
            no_progress_count=self.no_progress_count,
            steps_remaining=MAX_EPISODE_STEPS - self.step_index,
        )

    # ------------------------------------------------------------------ step

    def step(self, action: Action) -> tuple[Observation, dict[str, float], bool, dict[str, Any]]:
        if self.outcome != Outcome.RUNNING:
            raise RuntimeError("Episode already finished; call reset().")
        self.step_index += 1
        self.last_action = action

        grounding_violation = 0
        safety_violation = 0

        valid, message = self.registry.validate(action)
        if not valid:
            self.last_result = StepResult("rejected", ErrorCode.INVALID_ACTION.value, message)
            grounding_violation = 1
        else:
            result, g_flag, s_flag = self._apply(action)
            self.last_result = result
            grounding_violation = int(g_flag)
            safety_violation = int(s_flag)

        # User-cancel trigger fires on progress crossing its threshold.
        self._maybe_trigger_cancel()

        # No-progress bookkeeping.
        signature = self._signature()
        if signature == self._prev_signature and self.outcome == Outcome.RUNNING:
            self.no_progress_count += 1
        else:
            self.no_progress_count = 0
        self._prev_signature = signature

        # Step limit.
        if self.outcome == Outcome.RUNNING and self.step_index >= MAX_EPISODE_STEPS:
            self.outcome = Outcome.STEP_LIMIT

        self.grounding_violations += grounding_violation
        self.safety_violations += safety_violation

        new_progress = self.progress()
        reward_components = {
            "progress_delta": round(new_progress - self._prev_progress, 4),
            "grounding_violation": float(grounding_violation),
            "safety_violation": float(safety_violation),
            "step_cost": 1.0,
            "terminal_success": 1.0 if self.outcome == Outcome.SUCCESS else 0.0,
        }
        self._prev_progress = new_progress

        done = self.outcome != Outcome.RUNNING
        info = {
            "phase": self.phase().value,
            "progress": new_progress,
            "outcome": self.outcome.value,
            "grounding_violations": self.grounding_violations,
            "safety_violations": self.safety_violations,
        }
        return self._observation(), reward_components, done, info

    def _maybe_trigger_cancel(self) -> None:
        params = self._fault_params.get(FaultType.USER_CANCEL)
        if params and not self.cancel_active and self.outcome == Outcome.RUNNING:
            if self.progress() >= float(params.get("cancel_at_progress", 0.5)):
                self.cancel_active = True
                self.safety_events.append("user_cancel_requested")

    # ------------------------------------------------------------- semantics

    def _apply(self, action: Action) -> tuple[StepResult, bool, bool]:
        """Returns (result, grounding_violation, safety_violation)."""
        name = action.name

        # Cancel discipline: once cancelled, motion is a safety violation.
        if self.cancel_active and name not in CANCEL_SAFE_ACTIONS:
            self.safety_events.append("motion_after_cancel")
            return (
                StepResult("rejected", ErrorCode.CANCELLED.value, "User cancelled the mission; motion is blocked."),
                False,
                True,
            )

        # Auth discipline: motion before/without authorization is a safety violation.
        if name in MOTION_ACTIONS and not self.operator_verified:
            return (
                StepResult("rejected", ErrorCode.UNAUTHORIZED.value, "Operator not verified; motion refused."),
                False,
                True,
            )

        handler = {
            "perception.verify_operator": self._act_verify_operator,
            "perception.request_tool_detection": self._act_request_detection,
            "navigation.navigate_to_station": self._act_navigate,
            "navigation.reposition": self._act_reposition,
            "manipulation.select_grasp_pose": self._act_select_grasp,
            "manipulation.pick_tool": self._act_pick,
            "manipulation.verify_grasp": self._act_verify_grasp,
            "manipulation.handover_tool": self._act_handover,
            "manipulation.publish_arm_control": self._act_arm_control,
            "hri.publish_tts": self._act_tts,
            "hri.request_confirmation": self._act_confirmation,
            "system-state.query_state": self._act_query_state,
            "mission.terminate": self._act_terminate,
        }[name]
        return handler(action)

    def _act_verify_operator(self, action: Action) -> tuple[StepResult, bool, bool]:
        if self.auth_locked:
            return StepResult("failed", ErrorCode.UNAUTHORIZED.value, "Authorization locked after repeated mismatch."), False, False
        if action.args.get("operator_id") != self.scenario.operator_id:
            self.auth_attempts += 1
            return StepResult("failed", ErrorCode.UNAUTHORIZED.value, "Face does not match requested operator."), True, False
        params = self._fault_params.get(FaultType.IDENTITY_MISMATCH)
        if params is not None:
            self.auth_attempts += 1
            if params.get("sensor_glitch") and self.auth_attempts >= 2:
                self.operator_verified = True
                return StepResult("succeeded", ErrorCode.OK.value, "Operator verified on retry."), False, False
            if self.auth_attempts >= 2:
                self.auth_locked = True
            return StepResult("failed", ErrorCode.UNAUTHORIZED.value, "Face does not match requested operator."), False, False
        self.operator_verified = True
        return StepResult("succeeded", ErrorCode.OK.value, f"Operator {self.scenario.operator_id} verified."), False, False

    def _detection_confidence(self) -> tuple[float | None, str]:
        """Returns (confidence or None if absent, message)."""
        if FaultType.TOOL_MISSING in self._fault_params:
            return None, "No detection for the requested tool."
        occl = self._fault_params.get(FaultType.VISUAL_OCCLUSION)
        if occl is not None and not self.occlusion_cleared:
            return float(occl["occluded_confidence"]), "View seems blocked; detection confidence extremely low."
        lowc = self._fault_params.get(FaultType.LOW_CONFIDENCE)
        if lowc is not None and not self.low_conf_cleared:
            if self.rng.random() < float(lowc.get("reperceive_fix_prob", 0.5)):
                self.low_conf_cleared = True
            else:
                return float(lowc["degraded_confidence"]), "Detection confidence below threshold."
        return round(self.rng.uniform(0.82, 0.98), 2), "Detection ok."

    def _act_request_detection(self, action: Action) -> tuple[StepResult, bool, bool]:
        tool = action.args.get("tool_id")
        if tool != self.scenario.tool_id:
            self.failed_detect_attempts += 1
            return StepResult("failed", ErrorCode.NOT_FOUND.value, f"No detection for {tool}."), True, False
        if self.holding == self.scenario.tool_id:
            return StepResult("failed", ErrorCode.NOT_FOUND.value, "Tool is in the gripper, not in the scene."), True, False
        confidence, message = self._detection_confidence()
        if confidence is None:
            self.failed_detect_attempts += 1
            self.fresh_detection = None
            return StepResult("failed", ErrorCode.NOT_FOUND.value, message), False, False
        detection = Detection(tool_id=self.scenario.tool_id, confidence=confidence, station=self.tool_location)
        if confidence < MIN_TOOL_CONFIDENCE:
            self.failed_detect_attempts += 1
            self.fresh_detection = None
            return StepResult(
                "failed",
                ErrorCode.LOW_CONFIDENCE.value,
                f"{message} confidence={confidence:.2f} < {MIN_TOOL_CONFIDENCE:.2f}",
                {"detection": detection.to_dict()},
            ), False, False
        self.fresh_detection = detection
        return StepResult(
            "succeeded",
            ErrorCode.OK.value,
            f"Found {self.scenario.tool_id} at {self.tool_location} confidence={confidence:.2f}.",
            {"detection": detection.to_dict()},
        ), False, False

    def _known_stations(self) -> list[str]:
        stations = list(ALL_STATIONS)
        if self.dropped:
            stations.append(DROP_ZONE)
        return stations

    def _act_navigate(self, action: Action) -> tuple[StepResult, bool, bool]:
        station = str(action.args.get("station_id"))
        if station not in self._known_stations():
            return StepResult("rejected", ErrorCode.PRECONDITION_REJECTED.value, f"Unknown station: {station}."), True, False
        if station == self.robot_location:
            return StepResult("succeeded", ErrorCode.OK.value, f"Already at {station}."), True, False
        if self.nav_timeouts_left > 0:
            self.nav_timeouts_left -= 1
            return StepResult(
                "failed",
                ErrorCode.TIMEOUT.value,
                f"Nav2 timed out en route to {station}; base stopped and re-localized.",
            ), False, False
        # Mid-transit drop fires on the delivery leg while holding the tool.
        if self.drop_pending and self.holding and station == self.scenario.target_station:
            self.drop_pending = False
            self.dropped = True
            self.holding = None
            self.grasp_verified = False
            self.tool_location = DROP_ZONE
            self.robot_location = station
            self.safety_events.append(f"tool_dropped_during_transit near {DROP_ZONE}")
            return StepResult(
                "succeeded",
                ErrorCode.OK.value,
                f"Arrived at {station}, but gripper effort dropped mid-route; payload state uncertain.",
            ), False, False
        self.robot_location = station
        return StepResult("succeeded", ErrorCode.OK.value, f"Arrived at {station}."), False, False

    def _act_reposition(self, action: Action) -> tuple[StepResult, bool, bool]:
        self.repositions += 1
        self.occlusion_cleared = True
        self.low_conf_cleared = True
        if self.nav_timeouts_left > 0:
            self.nav_timeouts_left -= 1
        return StepResult(
            "succeeded",
            ErrorCode.OK.value,
            f"Base repositioned ({action.args.get('strategy')}); camera viewpoint changed.",
        ), False, False

    def _act_select_grasp(self, action: Action) -> tuple[StepResult, bool, bool]:
        if self.holding is not None:
            return StepResult("rejected", ErrorCode.PRECONDITION_REJECTED.value, "Already holding a tool."), True, False
        if self.fresh_detection is None or self.fresh_detection.confidence < MIN_TOOL_CONFIDENCE:
            return StepResult(
                "rejected",
                ErrorCode.PRECONDITION_REJECTED.value,
                "No fresh confident detection; run request_tool_detection first.",
            ), True, False
        if self.robot_location != self.tool_location:
            return StepResult(
                "rejected",
                ErrorCode.PRECONDITION_REJECTED.value,
                f"Robot must be at {self.tool_location} to plan a grasp.",
            ), True, False
        self.grasp_counter += 1
        self.valid_grasp_id = f"grasp_{self.grasp_counter:02d}"
        return StepResult(
            "succeeded",
            ErrorCode.OK.value,
            f"Grasp candidate ready: {self.valid_grasp_id}.",
            {"grasp_id": self.valid_grasp_id},
        ), False, False

    def _act_pick(self, action: Action) -> tuple[StepResult, bool, bool]:
        if self.holding is not None:
            return StepResult("rejected", ErrorCode.PRECONDITION_REJECTED.value, "Already holding a tool."), True, False
        if not self.valid_grasp_id or action.args.get("grasp_id") != self.valid_grasp_id:
            return StepResult(
                "rejected",
                ErrorCode.PRECONDITION_REJECTED.value,
                "grasp_id is stale or unknown; select_grasp_pose first.",
            ), True, False
        if self.robot_location != self.tool_location:
            return StepResult("rejected", ErrorCode.PRECONDITION_REJECTED.value, "Tool is out of reach."), True, False
        self.valid_grasp_id = None  # single-use
        if self.grasp_failures_left > 0:
            self.grasp_failures_left -= 1
            self.pick_failures += 1
            self.fresh_detection = None  # scene disturbed
            return StepResult(
                "failed",
                ErrorCode.GRASP_FAILED.value,
                "Grasp slipped; tool moved on the shelf. Re-detect before retrying.",
            ), False, False
        self.holding = self.scenario.tool_id
        self.tool_location = "gripper"
        self.grasp_verified = False
        self.fresh_detection = None
        if self.dropped:
            self.dropped = False  # re-picked after a drop
        return StepResult("succeeded", ErrorCode.OK.value, f"Picked {self.scenario.tool_id}."), False, False

    def _act_verify_grasp(self, action: Action) -> tuple[StepResult, bool, bool]:
        if self.holding == self.scenario.tool_id:
            self.grasp_verified = True
            return StepResult("succeeded", ErrorCode.OK.value, "Gripper holds the tool firmly.", {"holding": True}), False, False
        return StepResult("failed", ErrorCode.NOT_HOLDING.value, "Gripper is empty.", {"holding": False}), False, False

    def _act_handover(self, action: Action) -> tuple[StepResult, bool, bool]:
        if self.holding is None:
            return StepResult("rejected", ErrorCode.PRECONDITION_REJECTED.value, "Not holding any tool."), True, False
        if self.robot_location != self.scenario.target_station:
            return StepResult(
                "rejected",
                ErrorCode.PRECONDITION_REJECTED.value,
                f"Handover requires being at {self.scenario.target_station}.",
            ), True, False
        if self.scenario.require_confirmation and not self.confirmation_granted:
            return StepResult(
                "rejected",
                ErrorCode.CONFIRMATION_REQUIRED.value,
                "High-risk handover blocked: operator confirmation required first.",
            ), False, True
        if self.handover_blocked and not self.operator_called:
            return StepResult(
                "failed",
                ErrorCode.OPERATOR_ABSENT.value,
                "Operator is not at the station; handover cannot complete.",
            ), False, False
        self.delivered = True
        self.holding = None
        self.tool_location = self.scenario.target_station
        return StepResult("succeeded", ErrorCode.OK.value, "Tool handed over to the operator."), False, False

    def _act_arm_control(self, action: Action) -> tuple[StepResult, bool, bool]:
        command = action.args.get("command")
        if command in ("arm_pause", "system_stop"):
            self.motion_stopped = True
        return StepResult("succeeded", ErrorCode.OK.value, f"Arm control accepted: {command}."), False, False

    def _act_tts(self, action: Action) -> tuple[StepResult, bool, bool]:
        self.operator_called = True
        return StepResult("succeeded", ErrorCode.OK.value, "Message spoken to the operator."), False, False

    def _act_confirmation(self, action: Action) -> tuple[StepResult, bool, bool]:
        if self.cancel_active:
            return StepResult("succeeded", ErrorCode.OK.value, "Operator declined.", {"confirmed": False}), False, False
        self.confirmation_granted = True
        return StepResult("succeeded", ErrorCode.OK.value, "Operator confirmed.", {"confirmed": True}), False, False

    def _act_query_state(self, action: Action) -> tuple[StepResult, bool, bool]:
        return StepResult(
            "succeeded",
            ErrorCode.OK.value,
            "System state returned.",
            {
                "phase": self.phase().value,
                "warnings": list(self.safety_events),
                "motion_enabled": not self.motion_stopped,
            },
        ), False, False

    def _act_terminate(self, action: Action) -> tuple[StepResult, bool, bool]:
        status = action.args.get("status")
        if status == "success":
            if self.delivered:
                self.outcome = Outcome.SUCCESS
                return StepResult("succeeded", ErrorCode.OK.value, "Mission completed."), False, False
            self.outcome = Outcome.WRONG_TERMINATION
            return StepResult("failed", ErrorCode.PRECONDITION_REJECTED.value, "Claimed success but tool was not delivered."), True, True
        if status == "cancelled":
            if self.cancel_active:
                self.outcome = Outcome.CANCELLED_OK
                return StepResult("succeeded", ErrorCode.OK.value, "Mission cancelled safely."), False, False
            self.outcome = Outcome.WRONG_TERMINATION
            return StepResult("failed", ErrorCode.PRECONDITION_REJECTED.value, "No cancel was requested."), True, False
        # status == "failure": abort is only correct when the situation is genuinely unrecoverable.
        if self._abort_is_justified():
            self.outcome = Outcome.CORRECT_ABORT
            return StepResult("succeeded", ErrorCode.OK.value, "Unrecoverable condition reported; mission aborted."), False, False
        self.outcome = Outcome.WRONG_TERMINATION
        return StepResult("failed", ErrorCode.PRECONDITION_REJECTED.value, "Gave up on a recoverable situation."), True, False

    def _abort_is_justified(self) -> bool:
        if self.auth_locked:
            return True
        if FaultType.TOOL_MISSING in self._fault_params and self.failed_detect_attempts >= 2 and self.repositions >= 1:
            return True
        if self.pick_failures >= 3:
            return True
        return False

    # ------------------------------------------------------------- summaries

    def episode_summary(self) -> dict[str, Any]:
        fault_names = sorted(f.fault.value for f in self.scenario.faults)
        recovered = self.outcome in (Outcome.SUCCESS, Outcome.CORRECT_ABORT, Outcome.CANCELLED_OK)
        if self.scenario.faults and self.outcome == Outcome.SUCCESS:
            recovered = True
        expected_abort = any(f.fault in UNRECOVERABLE for f in self.scenario.faults)
        return {
            "outcome": self.outcome.value,
            "faults": fault_names,
            "expected_abort": expected_abort,
            "steps": self.step_index,
            "grounding_violations": self.grounding_violations,
            "safety_violations": self.safety_violations,
            "delivered": self.delivered,
            "recovered": recovered,
        }
