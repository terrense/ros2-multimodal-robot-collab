"""Non-LLM policies: the expert oracle and the hand-written rule baseline.

`ExpertPolicy` is the data-generation oracle. It has one recovery rule per fault
family and, by construction, recovers from all of them. It is not a baseline:
comparing a trained model against it would compare against the labels.

`RuleBaselinePolicy` is the reference point. It is a deliberate reconstruction of
what the deployed system does -- a fixed happy path plus a small table of
recovery rules written for the faults that were anticipated. Its coverage flags
are the whole point: single anticipated faults are handled (detection retry,
viewpoint change, navigation retry, re-detect after a slipped grasp), while
situations nobody wrote a rule for are not (a tool dropped in transit, an absent
operator, two faults at once). That gap is what post-training is supposed to
close, and `RuleCoverage` makes it explicit and adjustable instead of implied.

Any number this policy produces is measured by running it, never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from agent_env.expert_policy import ScriptedExpert
from agent_env.l1_sim import DROP_ZONE, PICKUP_STATION
from agent_env.protocol import Action, ErrorCode, Observation

from ..chat_format import action_content
from ..envs import observation_from_messages
from .base import BasePolicy, Messages

MIN_CONFIDENCE = 0.70


@dataclass(frozen=True)
class RuleCoverage:
    """Which recovery rules the hand-written system actually implements."""

    auth_retries: int = 1
    detect_retries: int = 2
    nav_retries: int = 2
    grasp_retries: int = 2
    reposition_on_detection_failure: bool = True  # anticipated: occlusion / low confidence
    redetect_after_grasp_failure: bool = True  # anticipated: slipped grasp
    verify_grasp_after_pick: bool = True
    ask_confirmation: bool = True
    respect_cancel: bool = True  # gesture-driven stop exists in the deployed system
    recover_dropped_tool: bool = False  # unanticipated: payload lost mid-transit
    call_operator_when_absent: bool = False  # unanticipated: nobody at the station
    reperceive_before_low_confidence_retry: bool = False  # unanticipated: retry the frame first

    @classmethod
    def full(cls) -> "RuleCoverage":
        """Every rule enabled; behaves like the expert on single faults."""
        return cls(
            recover_dropped_tool=True,
            call_operator_when_absent=True,
            reperceive_before_low_confidence_retry=True,
        )

    def without(self, **flags: bool) -> "RuleCoverage":
        return replace(self, **flags)


class ExpertPolicy(BasePolicy):
    """The scripted expert, driven through the same chat surface as any LLM policy."""

    name = "expert"
    stateful = True

    def __init__(self) -> None:
        self._expert: ScriptedExpert | None = None

    def reset(self) -> None:
        self._expert = None

    def act_batch(self, batch: Sequence[Messages]) -> list[str]:
        return [self._act_one(messages) for messages in batch]

    def _act_one(self, messages: Messages) -> str:
        observation = observation_from_messages(messages)
        if observation.step_index == 0 or self._expert is None:
            self._expert = ScriptedExpert()
        return action_content(self._expert.act(observation))


class RuleBaselinePolicy(BasePolicy):
    """Fixed happy path + a bounded recovery table. One instance per episode."""

    name = "rule_baseline"
    stateful = True

    def __init__(self, coverage: RuleCoverage | None = None) -> None:
        self.coverage = coverage or RuleCoverage()
        self.reset()

    def reset(self) -> None:
        self.auth_attempts = 0
        self.detect_failures = 0
        self.nav_failures = 0
        self.grasp_failures = 0
        self.handover_failures = 0
        self.repositioned = False
        self.stopped = False
        self.confirmed = False
        self.pending_grasp_id: str | None = None
        self.tool_station = PICKUP_STATION

    def act_batch(self, batch: Sequence[Messages]) -> list[str]:
        return [self._act_one(messages) for messages in batch]

    def _act_one(self, messages: Messages) -> str:
        observation = observation_from_messages(messages)
        if observation.step_index == 0:
            self.reset()
        return action_content(self.decide(observation))

    # ----------------------------------------------------------------- rules

    def decide(self, observation: Observation) -> Action:
        """The rule table itself. Named apart from `act` so it never shadows the
        text-level `BasePolicy.act(messages)` the rollout driver calls."""
        task = observation.task
        result = observation.last_result
        last = observation.last_action
        last_name = f"{last['skill']}.{last['action']}" if last else ""

        if "user_cancel_requested" in observation.safety_events:
            return self._on_cancel()

        if not observation.operator_verified:
            return self._on_identity(task, last_name, result)

        if last_name == "manipulation.select_grasp_pose" and result.status == "succeeded":
            self.pending_grasp_id = str(result.data.get("grasp_id"))
        if last_name == "manipulation.handover_tool" and result.status == "succeeded":
            return Action("mission", "terminate", {"status": "success", "reason": "Tool handed over."}, "Done.")
        if last_name == "hri.request_confirmation" and result.data.get("confirmed"):
            self.confirmed = True

        if observation.holding_tool == task["tool_id"]:
            return self._on_delivery(observation, task, last_name, result)
        return self._on_acquire(observation, task, last_name, result)

    def _on_cancel(self) -> Action:
        if self.coverage.respect_cancel and not self.stopped:
            self.stopped = True
            return Action("manipulation", "publish_arm_control", {"command": "system_stop"}, "Cancel received; stop motion.")
        return Action("mission", "terminate", {"status": "cancelled", "reason": "Operator cancelled the mission."}, "Cancelled.")

    def _on_identity(self, task, last_name: str, result) -> Action:
        if last_name == "perception.verify_operator" and result.status != "succeeded":
            self.auth_attempts += 1
            if self.auth_attempts > self.coverage.auth_retries:
                return Action(
                    "mission",
                    "terminate",
                    {"status": "failure", "reason": "Operator identity could not be verified."},
                    "Authorization exhausted.",
                )
        return Action("perception", "verify_operator", {"operator_id": task["operator_id"]}, "Verify the operator.")

    def _on_delivery(self, observation: Observation, task, last_name: str, result) -> Action:
        if self.coverage.verify_grasp_after_pick and last_name == "manipulation.pick_tool" and result.status == "succeeded":
            return Action("manipulation", "verify_grasp", {"tool_id": task["tool_id"]}, "Confirm the grasp.")
        if observation.robot_location != task["target_station"]:
            return self._navigate(task["target_station"], "Deliver the tool.", last_name, result)
        if task.get("require_confirmation") and self.coverage.ask_confirmation and not self.confirmed:
            return Action(
                "hri",
                "request_confirmation",
                {"question": f"Ready to hand over {task['tool_id']}. Confirm?"},
                "Confirmation required before handover.",
            )
        if last_name == "manipulation.handover_tool" and result.error_code == ErrorCode.OPERATOR_ABSENT.value:
            self.handover_failures += 1
            if self.coverage.call_operator_when_absent:
                return Action(
                    "hri",
                    "publish_tts",
                    {"text": f"{task['operator_id']}, your {task['tool_id']} is at {task['target_station']}."},
                    "Call the operator to the station.",
                )
            if self.handover_failures > 2:
                return Action(
                    "mission",
                    "terminate",
                    {"status": "failure", "reason": "Handover did not complete."},
                    "No rule for an absent operator.",
                )
        return Action("manipulation", "handover_tool", {"operator_id": task["operator_id"]}, "Hand the tool over.")

    def _on_acquire(self, observation: Observation, task, last_name: str, result) -> Action:
        tool_id = task["tool_id"]

        if any(event.startswith("tool_dropped") for event in observation.safety_events) and observation.holding_tool is None:
            if not self.coverage.recover_dropped_tool:
                return Action(
                    "mission",
                    "terminate",
                    {"status": "failure", "reason": "The tool was lost in transit."},
                    "No rule for recovering a dropped tool.",
                )
            self.tool_station = DROP_ZONE

        if last_name == "manipulation.pick_tool" and result.error_code == ErrorCode.GRASP_FAILED.value:
            self.grasp_failures += 1
            self.pending_grasp_id = None
            if self.grasp_failures > self.coverage.grasp_retries:
                return Action(
                    "mission",
                    "terminate",
                    {"status": "failure", "reason": "The manipulator could not grasp the tool."},
                    "Grasp retries exhausted.",
                )
            if not self.coverage.redetect_after_grasp_failure:
                # The gap this flag models: retry the same grasp on a disturbed scene.
                return Action(
                    "manipulation",
                    "pick_tool",
                    {"tool_id": tool_id, "grasp_id": "grasp_01"},
                    "Retry the grasp.",
                )

        if last_name == "perception.request_tool_detection" and result.status == "failed":
            self.detect_failures += 1
            if (
                result.error_code == ErrorCode.LOW_CONFIDENCE.value
                and self.coverage.reperceive_before_low_confidence_retry
                and self.detect_failures == 1
            ):
                return Action("perception", "request_tool_detection", {"tool_id": tool_id}, "Take a fresh frame.")
            if self.coverage.reposition_on_detection_failure and not self.repositioned:
                self.repositioned = True
                return Action(
                    "navigation",
                    "reposition",
                    {"strategy": "viewpoint_left", "reason": "Detection failed; change viewpoint."},
                    "Change the camera viewpoint.",
                )
            if self.detect_failures > self.coverage.detect_retries:
                return Action(
                    "mission",
                    "terminate",
                    {"status": "failure", "reason": f"{tool_id} could not be located."},
                    "Detection retries exhausted.",
                )

        detection = next((d for d in observation.detections if d.tool_id == tool_id and d.confidence >= MIN_CONFIDENCE), None)
        if detection is None:
            return Action("perception", "request_tool_detection", {"tool_id": tool_id}, "Locate the tool.")

        self.tool_station = detection.station
        if observation.robot_location != detection.station:
            return self._navigate(detection.station, f"Approach {detection.station}.", last_name, result)
        if self.pending_grasp_id is None or last_name == "manipulation.pick_tool":
            return Action("manipulation", "select_grasp_pose", {"tool_id": tool_id}, "Plan a grasp.")
        grasp_id = self.pending_grasp_id
        self.pending_grasp_id = None
        return Action("manipulation", "pick_tool", {"tool_id": tool_id, "grasp_id": grasp_id}, "Execute the grasp.")

    def _navigate(self, station: str, why: str, last_name: str, result) -> Action:
        if last_name == "navigation.navigate_to_station" and result.error_code == ErrorCode.TIMEOUT.value:
            self.nav_failures += 1
            if self.nav_failures > self.coverage.nav_retries:
                return Action(
                    "mission",
                    "terminate",
                    {"status": "failure", "reason": "Navigation kept timing out."},
                    "Navigation retries exhausted.",
                )
        return Action("navigation", "navigate_to_station", {"station_id": station, "reason": why}, why)


__all__ = ["ExpertPolicy", "RuleBaselinePolicy", "RuleCoverage"]
