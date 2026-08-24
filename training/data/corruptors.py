"""Plausible-but-worse alternatives to the expert's action.

Each rule targets one failure mode the deployed prompt/rule system actually
showed: blind retry loops, giving up on a recoverable fault, claiming success
before delivering, moving before authorization, acting on a stale detection,
inventing a station, ignoring a cancel.

A rule only *proposes*. Whether the proposal really is worse is decided by
`build_dpo` by executing it in a cloned environment and scoring both branches
with the same composite reward that PPO later optimizes. Rules that turn out to
be no worse than the expert are dropped, so no preference pair rests on the
assumption that a hand-written "bad" action was in fact bad.

`persist=True` marks rules whose damage is the loop they start: on the rejected
branch the same call is repeated, which is what turns
"grasp failed -> retry -> retry -> retry" into a contrastive pair against
"grasp failed -> re-detect -> reposition -> retry".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_env.l1_sim import ALL_STATIONS
from agent_env.protocol import Action, ErrorCode, Observation

ProposeFn = Callable[[Observation, Action], Action | None]

#: Stations that do not exist in the registry; used by the hallucination rule.
FAKE_STATIONS = ("storage_room_b", "corridor_north", "tool_shelf_2")


@dataclass(frozen=True)
class CorruptionRule:
    name: str
    tag: str  # failure mode this rule reproduces
    propose: ProposeFn
    persist: bool = False  # repeat the bad call on the rejected branch


def _last_name(observation: Observation) -> str:
    action = observation.last_action
    return f"{action['skill']}.{action['action']}" if action else ""


def _failed(observation: Observation) -> bool:
    return observation.last_result.status in ("failed", "rejected")


def _blind_retry(observation: Observation, expert: Action) -> Action | None:
    """Repeat the call that just failed, unchanged."""
    if not _failed(observation) or not observation.last_action:
        return None
    previous = Action.from_dict(observation.last_action)
    if previous.name == expert.name and previous.args == expert.args:
        return None  # the expert is retrying on purpose (e.g. auth sensor glitch)
    return Action(previous.skill, previous.action, dict(previous.args), "Try the same thing again.")


def _premature_failure(observation: Observation, expert: Action) -> Action | None:
    """Abort while the situation is still recoverable."""
    if expert.name == "mission.terminate":
        return None
    if not _failed(observation) and observation.no_progress_count == 0:
        return None
    return Action(
        "mission",
        "terminate",
        {"status": "failure", "reason": "The last step failed, so the mission cannot continue."},
        "Something went wrong; stop here.",
    )


def _false_success(observation: Observation, expert: Action) -> Action | None:
    """Claim the mission is done before the handover happened."""
    if expert.name == "mission.terminate":
        return None
    if observation.holding_tool is None and not observation.operator_verified:
        return None
    return Action(
        "mission",
        "terminate",
        {"status": "success", "reason": "The tool has been delivered."},
        "Looks close enough to done.",
    )


def _skip_confirmation(observation: Observation, expert: Action) -> Action | None:
    """Hand the tool over without asking, on a mission that requires confirmation."""
    if not observation.task.get("require_confirmation"):
        return None
    if observation.holding_tool is None:
        return None
    if expert.name == "manipulation.handover_tool":
        return None
    return Action(
        "manipulation",
        "handover_tool",
        {"operator_id": observation.task.get("operator_id", "")},
        "Hand it over right away.",
    )


def _unauthorized_motion(observation: Observation, expert: Action) -> Action | None:
    """Drive off before the operator has been verified."""
    if observation.operator_verified:
        return None
    return Action(
        "navigation",
        "navigate_to_station",
        {"station_id": "tool_shelf", "reason": "Start fetching the tool."},
        "Save time by driving while identity is checked.",
    )


def _hallucinated_station(observation: Observation, expert: Action) -> Action | None:
    """Navigate to a station that is not in the registry."""
    if expert.name != "navigation.navigate_to_station":
        return None
    station = FAKE_STATIONS[len(observation.safety_events) % len(FAKE_STATIONS)]
    if station in ALL_STATIONS:  # pragma: no cover - guards against a future rename
        return None
    return Action(
        "navigation",
        "navigate_to_station",
        {"station_id": station, "reason": "The tool is probably there."},
        "Guess a location instead of perceiving one.",
    )


def _ignore_cancel(observation: Observation, expert: Action) -> Action | None:
    """Keep moving after the user cancelled."""
    if "user_cancel_requested" not in observation.safety_events:
        return None
    return Action(
        "navigation",
        "navigate_to_station",
        {"station_id": observation.task.get("target_station", "station_a"), "reason": "Finish the delivery anyway."},
        "Almost done, so keep going.",
    )


def _stale_grasp(observation: Observation, expert: Action) -> Action | None:
    """Execute a grasp with an id that was never issued or was already consumed."""
    if observation.holding_tool is not None:
        return None
    if expert.name == "manipulation.pick_tool":
        return None
    return Action(
        "manipulation",
        "pick_tool",
        {"tool_id": observation.task.get("tool_id", ""), "grasp_id": "grasp_01"},
        "Reuse the earlier grasp candidate.",
    )


def _grasp_without_detection(observation: Observation, expert: Action) -> Action | None:
    """Plan a grasp with no fresh, confident detection to plan from."""
    if observation.detections:
        return None
    if observation.holding_tool is not None:
        return None
    if expert.name == "manipulation.select_grasp_pose":
        return None
    return Action(
        "manipulation",
        "select_grasp_pose",
        {"tool_id": observation.task.get("tool_id", "")},
        "The tool was there a moment ago.",
    )


def _wrong_tool(observation: Observation, expert: Action) -> Action | None:
    """Perceive the wrong object."""
    if expert.name != "perception.request_tool_detection":
        return None
    requested = observation.task.get("tool_id", "")
    other = "screwdriver" if requested != "screwdriver" else "wrench"
    return Action("perception", "request_tool_detection", {"tool_id": other}, "Look for a similar tool.")


def _schema_violation(observation: Observation, expert: Action) -> Action | None:
    """The expert's own call with a required argument dropped."""
    if not expert.args:
        return None
    dropped = sorted(expert.args)[0]
    args = {key: value for key, value in expert.args.items() if key != dropped}
    return Action(expert.skill, expert.action, args, f"Omit {dropped}.")


def _redundant_query(observation: Observation, expert: Action) -> Action | None:
    """Ask the system how it is doing instead of acting on what is already known."""
    if expert.name == "system-state.query_state":
        return None
    if observation.last_result.error_code not in (ErrorCode.OK.value, ""):
        return None
    return Action("system-state", "query_state", {}, "Check the state once more before acting.")


RULES: tuple[CorruptionRule, ...] = (
    CorruptionRule("blind_retry", "repeated_failing_call", _blind_retry, persist=True),
    CorruptionRule("premature_failure", "premature_stop", _premature_failure),
    CorruptionRule("false_success", "wrong_termination", _false_success),
    CorruptionRule("skip_confirmation", "safety_violation", _skip_confirmation),
    CorruptionRule("unauthorized_motion", "safety_violation", _unauthorized_motion),
    CorruptionRule("hallucinated_station", "invalid_grounding", _hallucinated_station),
    CorruptionRule("ignore_cancel", "safety_violation", _ignore_cancel),
    CorruptionRule("stale_grasp", "observation_misuse", _stale_grasp, persist=True),
    CorruptionRule("grasp_without_detection", "observation_misuse", _grasp_without_detection, persist=True),
    CorruptionRule("wrong_tool", "observation_misuse", _wrong_tool, persist=True),
    CorruptionRule("schema_violation", "invalid_grounding", _schema_violation),
    CorruptionRule("redundant_query", "no_progress", _redundant_query, persist=True),
)

RULES_BY_NAME: dict[str, CorruptionRule] = {rule.name: rule for rule in RULES}


def propose_all(observation: Observation, expert: Action) -> list[tuple[CorruptionRule, Action]]:
    """Every rule that applies to this state, in declaration order."""
    proposals: list[tuple[CorruptionRule, Action]] = []
    for rule in RULES:
        candidate = rule.propose(observation, expert)
        if candidate is None:
            continue
        if candidate.name == expert.name and candidate.args == expert.args:
            continue  # never contrast the expert with itself
        proposals.append((rule, candidate))
    return proposals


__all__ = ["CorruptionRule", "FAKE_STATIONS", "RULES", "RULES_BY_NAME", "propose_all"]
