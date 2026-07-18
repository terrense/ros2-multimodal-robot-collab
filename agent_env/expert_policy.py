"""Scripted expert for SFT data generation.

Normal flow mirrors mission_state_machine's canonical order; each fault
family has one hand-written recovery rule. The expert only reads
Observations (symptoms), never the injected fault types.
"""

from __future__ import annotations

from .l1_sim import DROP_ZONE
from .protocol import Action, ErrorCode, Observation


class ScriptedExpert:
    """One instance per episode; call act(obs) until the episode ends."""

    def __init__(self) -> None:
        self.auth_retried = False
        self.detect_failures = 0
        self.repositioned = False
        self.nav_timeouts = 0
        self.pick_attempts = 0
        self.stop_sent = False
        self.called_operator = False
        self.confirmation_granted = False
        self.tool_station: str | None = None  # None until first confident detection
        self.pending_grasp_id: str | None = None

    # ------------------------------------------------------------------ act

    def act(self, obs: Observation) -> Action:
        task = obs.task
        last = obs.last_result
        last_name = f"{obs.last_action['skill']}.{obs.last_action['action']}" if obs.last_action else ""

        # --- User cancel: stop motion, then terminate as cancelled.
        if "user_cancel_requested" in obs.safety_events:
            if not self.stop_sent:
                self.stop_sent = True
                return Action(
                    "manipulation", "publish_arm_control",
                    {"command": "system_stop"},
                    "User requested cancel; halting all motion first.",
                )
            return Action(
                "mission", "terminate",
                {"status": "cancelled", "reason": "User cancelled the mission; motion stopped safely."},
                "Cancel acknowledged after stopping motion.",
            )

        # --- Identity: verify, retry once, then abort. Never move unauthorized.
        if not obs.operator_verified:
            if last_name == "perception.verify_operator" and last.error_code == ErrorCode.UNAUTHORIZED.value:
                if not self.auth_retried:
                    self.auth_retried = True
                    return Action(
                        "perception", "verify_operator",
                        {"operator_id": task["operator_id"]},
                        "Verification failed once; retrying in case of a sensor glitch.",
                    )
                return Action(
                    "mission", "terminate",
                    {"status": "failure", "reason": "Operator identity could not be verified; refusing to proceed."},
                    "Two verification attempts failed; aborting is the safe choice.",
                )
            return Action(
                "perception", "verify_operator",
                {"operator_id": task["operator_id"]},
                "Verify the requesting operator before any motion.",
            )

        # --- Track events and results.
        if any(event.startswith("tool_dropped") for event in obs.safety_events) and obs.holding_tool is None:
            self.tool_station = DROP_ZONE

        if last_name == "manipulation.select_grasp_pose" and last.status == "succeeded":
            self.pending_grasp_id = str(last.data.get("grasp_id"))
        if last_name == "manipulation.handover_tool" and last.status == "succeeded":
            return Action(
                "mission", "terminate",
                {"status": "success", "reason": "Tool delivered and handed over."},
                "Handover confirmed; mission complete.",
            )
        if last_name == "hri.request_confirmation" and last.status == "succeeded" and last.data.get("confirmed"):
            self.confirmation_granted = True

        # --- Holding the tool: verify grasp, go to target, confirm, hand over.
        if obs.holding_tool == task["tool_id"]:
            if self._needs_grasp_check(last_name, last.status):
                return Action(
                    "manipulation", "verify_grasp",
                    {"tool_id": task["tool_id"]},
                    "Confirm the tool is held before moving.",
                )
            if obs.robot_location != task["target_station"]:
                return self._navigate(task["target_station"], "Deliver the tool to the operator's station.", last, last_name)
            if task.get("require_confirmation") and not self.confirmation_granted:
                return Action(
                    "hri", "request_confirmation",
                    {"question": f"Ready to hand over {task['tool_id']}. Confirm handover?"},
                    "Handover is a high-risk action and this mission requires confirmation.",
                )
            if last_name == "manipulation.handover_tool" and last.error_code == ErrorCode.OPERATOR_ABSENT.value:
                self.called_operator = True
                return Action(
                    "hri", "publish_tts",
                    {"text": f"{task['operator_id']}, your {task['tool_id']} has arrived at {task['target_station']}."},
                    "Operator absent; call them to the station before retrying handover.",
                )
            return Action(
                "manipulation", "handover_tool",
                {"operator_id": task["operator_id"]},
                "At the target station with the tool; perform the handover.",
            )

        # --- Not holding: acquire the tool (detect -> approach -> grasp).
        return self._acquire(obs, last, last_name)

    # ------------------------------------------------------------ sub-flows

    def _needs_grasp_check(self, last_name: str, status: str) -> bool:
        return last_name == "manipulation.pick_tool" and status == "succeeded"

    def _navigate(self, station: str, why: str, last, last_name: str) -> Action:
        if last_name == "navigation.navigate_to_station" and last.error_code == ErrorCode.TIMEOUT.value:
            self.nav_timeouts += 1
            if self.nav_timeouts >= 2:
                self.nav_timeouts = 0
                return Action(
                    "navigation", "reposition",
                    {"strategy": "step_back", "reason": "Two navigation timeouts; back off to unblock the planner."},
                    "Repeated Nav2 timeout; change pose before replanning.",
                )
        return Action("navigation", "navigate_to_station", {"station_id": station, "reason": why}, why)

    def _acquire(self, obs: Observation, last, last_name: str) -> Action:
        task = obs.task
        tool_id = task["tool_id"]

        # React to a failed pick: re-detect, new grasp pose, bounded retries.
        if last_name == "manipulation.pick_tool" and last.error_code == ErrorCode.GRASP_FAILED.value:
            self.pick_attempts += 1
            self.pending_grasp_id = None
            if self.pick_attempts >= 3:
                return Action(
                    "mission", "terminate",
                    {"status": "failure", "reason": "Three grasp attempts failed; manipulator cannot pick this tool."},
                    "Grasp retries exhausted; report instead of looping.",
                )

        # React to detection problems.
        if last_name == "perception.request_tool_detection" and last.status == "failed":
            self.detect_failures += 1
            if last.error_code == ErrorCode.LOW_CONFIDENCE.value and self.detect_failures == 1:
                return Action(
                    "perception", "request_tool_detection",
                    {"tool_id": tool_id},
                    "Confidence was low; take a fresh frame before moving the base.",
                )
            if not self.repositioned:
                self.repositioned = True
                return Action(
                    "navigation", "reposition",
                    {"strategy": "viewpoint_left", "reason": "Detection keeps failing; change the camera viewpoint."},
                    "Occlusion or degraded view suspected; reposition then re-perceive.",
                )
            if self.detect_failures >= 2:
                if not self.called_operator:
                    self.called_operator = True
                    return Action(
                        "hri", "publish_tts",
                        {"text": f"I cannot find the {tool_id} on the shelf. Please check its location."},
                        "Tool appears to be missing; inform the operator before aborting.",
                    )
                return Action(
                    "mission", "terminate",
                    {"status": "failure", "reason": f"{tool_id} not found after re-perception and repositioning."},
                    "Detection exhausted from multiple viewpoints; the tool is missing.",
                )

        # Fresh confident detection available?
        detection = next((d for d in obs.detections if d.tool_id == tool_id and d.confidence >= 0.70), None)
        if detection is None:
            return Action(
                "perception", "request_tool_detection",
                {"tool_id": tool_id},
                "Locate the tool before planning any motion.",
            )

        self.tool_station = detection.station
        if obs.robot_location != detection.station:
            return self._navigate(detection.station, f"Approach {detection.station} to grasp {tool_id}.", last, last_name)

        if self.pending_grasp_id is None or last_name == "manipulation.pick_tool":
            return Action(
                "manipulation", "select_grasp_pose",
                {"tool_id": tool_id},
                "Plan a grasp candidate from the fresh detection.",
            )
        grasp_id = self.pending_grasp_id
        self.pending_grasp_id = None
        return Action(
            "manipulation", "pick_tool",
            {"tool_id": tool_id, "grasp_id": grasp_id},
            "Execute the planned grasp.",
        )
