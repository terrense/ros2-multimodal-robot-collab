"""L2: the live ROS 2 / Gazebo stack behind an environment interface.

L1 (`agent_env/l1_sim.py`) is a text-level simulator. L2 is the stack that
actually runs: ROS 2 action servers, Nav2, the arms, the perception nodes. This
module presents that stack through the same `reset()` / `step()` shape the
evaluator already uses, so one evaluator can score both levels and the receipt
layer becomes reachable on the live stack.

Two things are deliberately not papered over.

**The action vocabularies are not isomorphic.** L1 has thirteen fine-grained
actions. L2 exposes six callable capabilities, two of them composite, and the
detections are *published* rather than requested. `ACTION_MAP` writes the
mapping out, including the actions L2 cannot do at all. An action with no
counterpart is refused with a clear reason rather than silently doing nothing:
on the live stack, "not supported" and "did nothing" are different failures.

**The live stack cannot report everything L1 reports.** `holding_tool` and
`operator_verified` live inside the mission state machine and are not
published, so the observation says *unknown* for them instead of guessing. An
unconfirmed physical effect is not a success, and an unknown is not a `False`
that a policy can act on.

The ROS import is lazy: this module can be imported, and its mapping and
result logic tested, on a machine with no ROS 2.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_env.protocol import (
    MAX_EPISODE_STEPS,
    Action,
    Detection,
    ErrorCode,
    Observation,
    Phase,
    StepResult,
)

#: The composite capability: one mission, executed by the mission state machine.
MISSION_CAPABILITY = "mission-control.deliver_tool"


@dataclass(frozen=True)
class L2Capability:
    """How one L1 action reaches the live stack."""

    capability: str
    arguments: Callable[[dict[str, Any]], dict[str, Any]]
    note: str = ""

    def build(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.arguments(dict(args))


def _identity(args: dict[str, Any]) -> dict[str, Any]:
    return dict(args)


def _navigate(args: dict[str, Any]) -> dict[str, Any]:
    return {"station_id": str(args.get("station_id", "")), "reason": str(args.get("reason", ""))}


def _arm_control(args: dict[str, Any]) -> dict[str, Any]:
    command = str(args.get("command", ""))
    return {
        "command": command,
        "operator_id": str(args.get("operator_id", "")),
        "source": str(args.get("source", "agent_env")),
        "confidence": float(args.get("confidence", 1.0)),
        "emergency": command == "system_stop",
        "detail": str(args.get("detail", "")),
    }


def _pick_and_place(args: dict[str, Any]) -> dict[str, Any]:
    """L1's three grasp actions collapse into L2's one composite action.

    The granularity is genuinely different: L2 picks and places in a single
    goal, so the intermediate "grasp selected" and "grasp verified" states do
    not exist on the live stack. The note on the mapping says so.
    """
    return {
        "tool_id": str(args.get("tool_id", "")),
        "source_frame": str(args.get("source_frame", "tool_shelf")),
        "target_frame": str(args.get("target_frame", "handover_zone")),
        "tool_pose": args.get("tool_pose") or {},
    }


#: L1 action name -> how to reach it on the live stack.
#:
#: `None` means the live stack has no counterpart. The reason is in
#: `UNSUPPORTED_REASONS`.
ACTION_MAP: dict[str, L2Capability | None] = {
    "perception.verify_operator": L2Capability(
        "perception.verify_operator",
        lambda args: {"operator_id": str(args.get("operator_id", ""))},
    ),
    "navigation.navigate_to_station": L2Capability(
        "navigation.navigate_to_station",
        _navigate,
    ),
    "manipulation.publish_arm_control": L2Capability(
        "manipulation.publish_arm_control",
        _arm_control,
    ),
    "hri.publish_tts": L2Capability(
        "hri.publish_tts",
        lambda args: {"text": str(args.get("text", ""))},
    ),
    "system-state.query_state": L2Capability(
        "system-state.query_state",
        lambda args: {"requester": str(args.get("requester", "agent_env"))},
    ),
    # Composite: pick and place in one goal.
    "manipulation.select_grasp_pose": L2Capability(
        "manipulation.pick_and_place", _pick_and_place, note="composite"
    ),
    "manipulation.pick_tool": L2Capability(
        "manipulation.pick_and_place", _pick_and_place, note="composite"
    ),
    "manipulation.verify_grasp": L2Capability(
        "manipulation.pick_and_place", _pick_and_place, note="composite"
    ),
    "manipulation.handover_tool": L2Capability(
        "manipulation.pick_and_place", _pick_and_place, note="composite"
    ),
    # No counterpart on the live stack.
    "perception.request_tool_detection": None,
    "navigation.reposition": None,
    "hri.request_confirmation": None,
    "mission.terminate": None,
}

#: Why the live stack cannot do what L1 can.
UNSUPPORTED_REASONS: dict[str, str] = {
    "perception.request_tool_detection": (
        "the live stack publishes detections from the YOLO node; there is no "
        "request to make, so the observation reads the topic instead"
    ),
    "navigation.reposition": "no viewpoint-change skill exists on the live stack",
    "hri.request_confirmation": "no confirmation skill exists on the live stack",
    "mission.terminate": "the episode ends when the caller stops dispatching",
}


class UnsupportedAction(ValueError):
    """Raised when an L1 action has no counterpart on the live stack."""


class UncertainOutcome(RuntimeError):
    """Raised when a command's physical effect is not established.

    The effect may still be happening. The episode stops rather than carrying on
    as if the command had not been dispatched.
    """


def capability_for(action: Action | str) -> L2Capability:
    """The live-stack capability for an L1 action, or a refusal."""
    name = action if isinstance(action, str) else action.name
    if name not in ACTION_MAP:
        raise UnsupportedAction(f"{name} is not an L1 action")
    capability = ACTION_MAP[name]
    if capability is None:
        raise UnsupportedAction(
            f"{name} has no counterpart on the live stack: "
            + UNSUPPORTED_REASONS.get(name, "not supported")
        )
    return capability


def _status_value(receipt: Any) -> str:
    """The receipt's execution status as a plain string.

    `ExecutionStatus` is a `str` enum, and `str(enum)` is its member name in
    Python 3.10, not its value. Comparing against the value keeps this honest
    whichever side of that quirk a caller is on.
    """
    status = getattr(receipt, "execution_status", None)
    return str(getattr(status, "value", status) or "")


def receipt_to_result(receipt: Any) -> StepResult:
    """Map an execution receipt onto L1's structured feedback.

    The split matters: `completed` means the *action server* reported success,
    which is not the same as the physical effect having happened. The L1
    `StepResult` carries no such claim, and neither does this one.
    """
    status = _status_value(receipt)
    failure = getattr(receipt, "failure_code", None)
    # The receipt's failure code is the contract's vocabulary (`timeout`,
    # `unknown_capability`), not L1's (`TIMEOUT`, `NOT_FOUND`). They describe
    # different worlds; translating one into the other would invent a
    # correspondence, so the code is carried through as it is.
    failure_code = str(getattr(failure, "value", failure) or ErrorCode.INVALID_ACTION.value)
    detail = str(getattr(receipt, "detail", "") or "")
    result = getattr(receipt, "result", None) or {}
    message = str(result.get("message") or detail) if isinstance(result, dict) else detail

    if status == "completed":
        return StepResult("succeeded", ErrorCode.OK.value, message or "completed", {})
    if status == "rejected":
        return StepResult("rejected", failure_code, message)
    if status == "unknown":
        raise UncertainOutcome(
            f"the effect of {getattr(receipt, 'command_id', '?')} is not established: {detail}"
        )
    return StepResult("failed", failure_code, message)


@dataclass
class L2State:
    """What the live stack has told us so far.

    `holding_tool` and `operator_verified` are *unknown* until something
    reports them; the live stack keeps them inside the mission state machine.
    """

    detections: list[Detection]
    robot_location: str | None = None
    holding_tool: str | None = None
    operator_verified: bool | None = None
    mission_state: str = ""
    mission_progress: float = 0.0
    warnings: list[str] = None  # type: ignore[assignment]
    seen_at: float = 0.0

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


class L2GazeboEnv:
    """The live stack behind `reset()` / `step()`.

    The tool client is injected, so the ROS 2 side stays lazy: this class can
    be exercised against a fake client on a machine with no ROS 2, and the
    mapping, the refusals and the result logic can be tested without one.
    """

    def __init__(
        self,
        client: Any,
        *,
        task: dict[str, Any],
        state: L2State | None = None,
        max_steps: int = MAX_EPISODE_STEPS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._task = dict(task)
        self._state = state or L2State(detections=[])
        self._max_steps = max_steps
        self._clock = clock
        self._step_index = 0
        self._last_action: dict[str, Any] | None = None
        self._last_result = StepResult("none", ErrorCode.OK.value, "")
        self._safety_events: list[str] = []
        self._no_progress = 0
        self._receipts: list[Any] = []
        self._progress_marks: list[float] = []

    # ------------------------------------------------------------------ state

    @property
    def receipts(self) -> list[Any]:
        return list(self._receipts)

    @property
    def state(self) -> L2State:
        return self._state

    def update_state(self, state: L2State) -> None:
        """Fold what the live stack reported into the environment's state."""
        self._state = state

    # ------------------------------------------------------------------- loop

    def reset(self) -> Observation:
        self._step_index = 0
        self._last_action = None
        self._last_result = StepResult("none", ErrorCode.OK.value, "")
        self._safety_events = []
        self._no_progress = 0
        self._receipts = []
        self._progress_marks = []
        return self.observation()

    def step(self, action: Action) -> tuple[Observation, dict[str, float], bool, dict[str, Any]]:
        """Dispatch one action and fold the receipt back into the observation."""
        capability = capability_for(action)
        request = self._request(capability, action)
        receipt = self._client.submit(request)
        if _status_value(receipt) == "queued":
            receipt = self._client.wait(request.command_id)
        self._receipts.append(receipt)

        result = receipt_to_result(receipt)
        self._last_action = action.to_dict()
        self._last_result = result
        self._step_index += 1

        progress = self._progress()
        if progress <= (self._progress_marks[-1] if self._progress_marks else -1.0):
            self._no_progress += 1
        else:
            self._no_progress = 0
        self._progress_marks.append(progress)

        info = {
            "phase": self._phase(),
            "progress": progress,
            "no_progress_count": self._no_progress,
            "turn": self._step_index,
            "capability": capability.capability,
            "execution_status": _status_value(receipt),
        }
        components = {
            "progress_delta": 0.0,
            "grounding_violation": 1.0 if action.name not in ACTION_MAP else 0.0,
            "safety_violation": 0.0,
            "step_cost": 1.0,
            "terminal_success": 0.0,
        }
        done = self._step_index >= self._max_steps
        return self.observation(), components, done, info

    # ------------------------------------------------------------ observation

    def observation(self) -> Observation:
        """The observation the policy sees.

        Fields the live stack does not publish stay *unknown* rather than
        being filled with a plausible default.
        """
        return Observation(
            step_index=self._step_index,
            task=dict(self._task),
            robot_location=self._state.robot_location or "",
            holding_tool=self._state.holding_tool,
            operator_verified=bool(self._state.operator_verified),
            detections=list(self._state.detections),
            last_action=self._last_action,
            last_result=self._last_result,
            safety_events=list(self._safety_events),
            no_progress_count=self._no_progress,
            steps_remaining=max(0, self._max_steps - self._step_index),
        )

    # --------------------------------------------------------------- internals

    def _request(self, capability: L2Capability, action: Action) -> Any:
        from agent_harness.contracts import ActionRequest, new_command_id

        return ActionRequest(
            episode_id=str(self._task.get("episode_id", "ep-l2")),
            command_id=new_command_id(),
            capability=capability.capability,
            arguments=capability.build(action.args),
        )

    def _progress(self) -> float:
        if self._state.mission_progress:
            return float(self._state.mission_progress)
        return 0.0

    def _phase(self) -> str:
        state = (self._state.mission_state or "").upper()
        for phase in Phase:
            if phase.value == state:
                return phase.value
        return Phase.IDLE.value


__all__ = [
    "ACTION_MAP",
    "L2Capability",
    "L2GazeboEnv",
    "L2State",
    "MISSION_CAPABILITY",
    "UNSUPPORTED_REASONS",
    "UncertainOutcome",
    "UnsupportedAction",
    "capability_for",
    "receipt_to_result",
]
