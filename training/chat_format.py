"""The single prompt surface: Observation -> chat messages, text -> Action.

Every stage (SFT corpus, DPO pairs, PPO rollout, OPD teacher scoring, evaluation)
renders prompts through this module, so a prompt edit can never silently make the
training distribution disagree with the evaluation distribution.

Message layout of one episode::

    system     action catalogue + decision rules (from ActionRegistry)
    user       task brief + observation JSON        (turn 1)
    assistant  {"skill":...,"action":...,"args":{...},"reason":"..."}
    user       observation JSON                     (turn 2, contains last_result)
    assistant  ...

Parsing is deliberately forgiving about *format* (code fences, Qwen3 <think>
blocks, `arguments` instead of `args`) and strictly unforgiving about *content*:
a repaired parse is still reported as `REPAIRED` so evaluation can separate
"model emitted a malformed action" from "model chose an illegal action".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from agent_env.protocol import Action, ActionRegistry, Observation

#: Emitted into the environment when the policy output cannot be parsed at all.
#: `ActionRegistry.validate` rejects it, so the env books a grounding violation
#: instead of the runner crashing mid-episode.
MALFORMED_ACTION = Action(skill="invalid", action="malformed_output", args={}, reason="unparseable policy output")

DECISION_RULES = """Decision rules:
- Emit exactly one JSON object per turn. No prose outside the JSON.
- Never invent skills, stations, tools or grasp ids. Use only what the observation and the catalogue give you.
- Verify the operator before any base or arm motion.
- After a failure, read last_result.error_code and change something. Repeating an identical failing call is the worst option.
- A stale detection is not evidence: re-perceive after any event that disturbed the scene.
- If the mission requires confirmation, ask before the handover.
- On user cancel: stop motion first (manipulation.publish_arm_control system_stop), then terminate with status=cancelled.
- terminate with status=success only after the tool was actually handed over.
- terminate with status=failure only when the situation is genuinely unrecoverable, and say why."""

RESPONSE_FORMAT = '{"skill": "<skill>", "action": "<action>", "args": {...}, "reason": "<one short sentence>"}'

_ACTION_KEY_ORDER = ("skill", "action", "args", "reason")
_ARGS_ALIASES = ("args", "arguments", "parameters", "params", "kwargs", "input")
_SKILL_ALIASES = ("skill", "skill_name", "namespace", "module")
_ACTION_ALIASES = ("action", "action_name", "name", "tool", "tool_name", "function", "call")
_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)
_FENCE = re.compile(r"```[a-zA-Z]*\s*(.*?)\s*```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


# --------------------------------------------------------------------- prompts


def system_prompt(registry: ActionRegistry | None = None, extra_rules: str = "") -> str:
    """Action catalogue + decision rules. Identical string in SFT, PPO and eval."""
    registry = registry or ActionRegistry.load()
    lines = [
        "You are the high-level decision module of a tool-delivery robot.",
        "A deterministic ROS2 layer executes your skill calls; you never emit velocities, trajectories or joint angles.",
        "At each step you receive one JSON observation and reply with exactly one JSON action:",
        RESPONSE_FORMAT,
        "",
        DECISION_RULES,
    ]
    if extra_rules:
        lines.extend(["", extra_rules.strip()])
    lines.extend(["", "Available actions:"])
    for name in registry.action_names:
        spec = registry.spec(name) or {}
        args = ", ".join(f"{key}: {value}" for key, value in (spec.get("args") or {}).items())
        confirm = " [requires confirmation]" if spec.get("requires_confirmation") else ""
        lines.append(f"- {name}({args}) risk={spec.get('risk')}{confirm} :: {spec.get('effect')}")
    return "\n".join(lines)


def task_brief(task: dict[str, Any]) -> str:
    """One-line natural-language restatement of the mission, prepended to turn 1."""
    confirmation = "required" if task.get("require_confirmation") else "not required"
    return (
        f"Mission: deliver {task.get('tool_id')} to {task.get('target_station')} "
        f"for {task.get('operator_id')}. Operator confirmation before handover: {confirmation}."
    )


def observation_content(observation: Observation | dict[str, Any], *, include_brief: bool | None = None) -> str:
    """User-turn content. `include_brief` defaults to True on the first step."""
    payload = observation.to_dict() if isinstance(observation, Observation) else dict(observation)
    if include_brief is None:
        include_brief = int(payload.get("step_index", 0)) == 0
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    if include_brief:
        return f"{task_brief(payload.get('task') or {})}\n\nOBSERVATION:\n{body}"
    return f"OBSERVATION:\n{body}"


def action_content(action: Action | dict[str, Any]) -> str:
    """Canonical assistant text. Key order is fixed so SFT targets are stable."""
    payload = action.to_dict() if isinstance(action, Action) else dict(action)
    ordered = {key: payload.get(key, {} if key == "args" else "") for key in _ACTION_KEY_ORDER}
    return json.dumps(ordered, ensure_ascii=False)


def build_messages(
    steps: list[dict[str, Any]],
    *,
    registry: ActionRegistry | None = None,
    system: str | None = None,
    max_history_turns: int | None = None,
) -> list[dict[str, str]]:
    """Render logged steps (`observation` + `action` per step) into a chat episode.

    `max_history_turns` keeps the system turn, the first user turn and the last N
    (observation, action) pairs; elided turns are replaced by one short marker so
    the model is told that history was dropped rather than silently rewritten.
    """
    messages = [{"role": "system", "content": system if system is not None else system_prompt(registry)}]
    total = len(steps)
    keep_from = 0 if max_history_turns is None else max(0, total - max_history_turns)
    for index, step in enumerate(steps):
        observation = step.get("observation") or {}
        if index and index < keep_from:
            continue
        if index and index == keep_from and keep_from > 0:
            messages.append(
                {"role": "user", "content": f"[{keep_from} earlier turns elided; the observation below is current]"}
            )
        messages.append({"role": "user", "content": observation_content(observation, include_brief=index == 0)})
        action = step.get("action")
        if action is not None:
            messages.append({"role": "assistant", "content": step.get("assistant_text") or action_content(action)})
    return messages


# --------------------------------------------------------------------- parsing


class ParseStatus(str, Enum):
    OK = "ok"  # clean single JSON object
    REPAIRED = "repaired"  # recovered from fences / think blocks / key aliases / trailing commas
    MALFORMED = "malformed"  # no action could be recovered


@dataclass
class ParsedAction:
    status: ParseStatus
    action: Action
    error: str = ""
    raw: str = ""

    @property
    def malformed(self) -> bool:
        return self.status is ParseStatus.MALFORMED


def parse_action(text: str) -> ParsedAction:
    """Text -> Action. Never raises; malformed output yields `MALFORMED_ACTION`."""
    raw = text or ""
    repaired = False

    body = raw
    if _THINK_CLOSE.search(body):  # Qwen3 thinking mode: keep only the post-</think> answer
        body = _THINK_CLOSE.split(body)[-1]
        repaired = True
    fenced = _FENCE.search(body)
    if fenced:
        body = fenced.group(1)
        repaired = True

    candidate = _extract_json_object(body)
    if candidate is None:
        return ParsedAction(ParseStatus.MALFORMED, MALFORMED_ACTION, "no JSON object found", raw)
    if candidate.strip() != body.strip():
        repaired = True

    data: Any = None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        patched = _TRAILING_COMMA.sub(r"\1", candidate)
        try:
            data = json.loads(patched)
            repaired = True
        except json.JSONDecodeError as exc:
            return ParsedAction(ParseStatus.MALFORMED, MALFORMED_ACTION, f"invalid JSON: {exc.msg}", raw)

    if not isinstance(data, dict):
        return ParsedAction(ParseStatus.MALFORMED, MALFORMED_ACTION, "top-level JSON is not an object", raw)

    normalized, key_repair = _normalize_action_dict(data)
    if normalized is None:
        return ParsedAction(ParseStatus.MALFORMED, MALFORMED_ACTION, "missing skill/action fields", raw)
    repaired = repaired or key_repair

    action = Action.from_dict(normalized)
    status = ParseStatus.REPAIRED if repaired else ParseStatus.OK
    return ParsedAction(status, action, "", raw)


def _extract_json_object(text: str) -> str | None:
    """First balanced top-level `{...}`, string- and escape-aware."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return None


def _normalize_action_dict(data: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    """Map common key/shape variants onto {skill, action, args, reason}."""
    repaired = False

    skill = next((data[key] for key in _SKILL_ALIASES if isinstance(data.get(key), str)), None)
    action = next((data[key] for key in _ACTION_ALIASES if isinstance(data.get(key), str)), None)
    if skill is None and isinstance(action, str) and "." in action:
        skill, action = action.split(".", 1)
        repaired = True
    elif isinstance(skill, str) and "." in skill and not action:
        skill, action = skill.split(".", 1)
        repaired = True
    elif isinstance(skill, str) and isinstance(action, str) and action.startswith(f"{skill}."):
        action = action.split(".", 1)[1]
        repaired = True
    if not skill or not action:
        return None, repaired
    if not any(key in data for key in ("skill", "action")):
        repaired = True

    args: Any = None
    for key in _ARGS_ALIASES:
        value = data.get(key)
        if isinstance(value, dict):
            args = value
            if key != "args":
                repaired = True
            break
        if isinstance(value, str) and value.strip().startswith("{"):
            try:
                args = json.loads(value)
                repaired = True
            except json.JSONDecodeError:
                args = None
            break
    if args is None:
        known = set(_ARGS_ALIASES) | set(_SKILL_ALIASES) | set(_ACTION_ALIASES) | {"reason", "thought", "rationale"}
        leftovers = {key: value for key, value in data.items() if key not in known}
        if leftovers:  # flat form: {"skill":..,"action":..,"station_id":".."}
            args = leftovers
            repaired = True
        else:
            args = {}

    reason = data.get("reason") or data.get("thought") or data.get("rationale") or ""
    return {"skill": str(skill), "action": str(action), "args": dict(args), "reason": str(reason)}, repaired


__all__ = [
    "DECISION_RULES",
    "MALFORMED_ACTION",
    "RESPONSE_FORMAT",
    "ParseStatus",
    "ParsedAction",
    "action_content",
    "build_messages",
    "observation_content",
    "parse_action",
    "system_prompt",
    "task_brief",
]
