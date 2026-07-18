"""Closed-loop observation/action protocol for the embodied delivery agent.

Unlike the open-loop mission plans in agent_harness (one blind JSON plan per
mission), training uses a step-level loop: the policy receives a structured
Observation, emits exactly one Action, and gets structured feedback merged
into the next Observation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

AGENT_ENV_ROOT = Path(__file__).resolve().parent
AGENT_SKILLS_SCHEMA = AGENT_ENV_ROOT / "schemas" / "agent_skills.json"

MIN_TOOL_CONFIDENCE = 0.70
MAX_EPISODE_STEPS = 30


class Phase(str, Enum):
    """Task phases, aligned with mission_state_machine states and used by the Progress Estimator."""

    IDLE = "IDLE"
    VERIFY_IDENTITY = "VERIFY_IDENTITY"
    DETECT_TOOL = "DETECT_TOOL"
    NAVIGATE_PICKUP = "NAVIGATE_PICKUP"
    PICK_TOOL = "PICK_TOOL"
    VERIFY_GRASP = "VERIFY_GRASP"
    NAVIGATE_DELIVER = "NAVIGATE_DELIVER"
    HANDOVER = "HANDOVER"
    DONE = "DONE"


PHASE_PROGRESS: dict[Phase, float] = {
    Phase.IDLE: 0.0,
    Phase.VERIFY_IDENTITY: 0.08,
    Phase.DETECT_TOOL: 0.22,
    Phase.NAVIGATE_PICKUP: 0.40,
    Phase.PICK_TOOL: 0.64,
    Phase.VERIFY_GRASP: 0.78,
    Phase.NAVIGATE_DELIVER: 0.86,
    Phase.HANDOVER: 0.96,
    Phase.DONE: 1.0,
}


class ErrorCode(str, Enum):
    OK = "OK"
    TIMEOUT = "TIMEOUT"
    NOT_FOUND = "NOT_FOUND"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    GRASP_FAILED = "GRASP_FAILED"
    NOT_HOLDING = "NOT_HOLDING"
    OPERATOR_ABSENT = "OPERATOR_ABSENT"
    UNAUTHORIZED = "UNAUTHORIZED"
    PRECONDITION_REJECTED = "PRECONDITION_REJECTED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    INVALID_ACTION = "INVALID_ACTION"
    CANCELLED = "CANCELLED"


class Outcome(str, Enum):
    """Episode-level outcome used by evaluation."""

    SUCCESS = "success"
    CORRECT_ABORT = "correct_abort"  # unrecoverable fault reported correctly
    CANCELLED_OK = "cancelled_ok"  # user cancel handled correctly
    WRONG_TERMINATION = "wrong_termination"  # e.g. claimed success without delivering
    STEP_LIMIT = "step_limit"
    RUNNING = "running"


@dataclass
class Action:
    """One step-level skill call emitted by the policy."""

    skill: str
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    @property
    def name(self) -> str:
        return f"{self.skill}.{self.action}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        return cls(
            skill=str(data.get("skill", "")),
            action=str(data.get("action", "")),
            args=dict(data.get("args") or {}),
            reason=str(data.get("reason", "")),
        )


@dataclass
class StepResult:
    """Structured feedback for the immediately preceding action."""

    status: str = "none"  # succeeded | failed | rejected | none
    error_code: str = ErrorCode.OK.value
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Detection:
    tool_id: str
    confidence: float
    station: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Observation:
    """Everything the policy is allowed to see at one step."""

    step_index: int
    task: dict[str, Any]  # tool_id, target_station, operator_id, require_confirmation
    robot_location: str
    holding_tool: str | None
    operator_verified: bool
    detections: list[Detection]
    last_action: dict[str, Any] | None
    last_result: StepResult
    safety_events: list[str]
    no_progress_count: int
    steps_remaining: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["last_result"] = self.last_result.to_dict()
        data["detections"] = [d.to_dict() for d in self.detections]
        return data

    def to_prompt_json(self) -> str:
        """Compact JSON rendering used as the tool/user turn content in chat samples."""
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class ActionRegistry:
    """Loads agent_skills.json and validates step-level actions (schema legality gate)."""

    def __init__(self, actions: dict[str, dict[str, Any]]) -> None:
        self._actions = actions

    @classmethod
    def load(cls, schema_path: Path = AGENT_SKILLS_SCHEMA) -> "ActionRegistry":
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        return cls(schema["actions"])

    @property
    def action_names(self) -> list[str]:
        return sorted(self._actions)

    def spec(self, name: str) -> dict[str, Any] | None:
        return self._actions.get(name)

    def validate(self, action: Action) -> tuple[bool, str]:
        """Returns (is_valid, message). Invalid actions are grounding violations."""
        spec = self._actions.get(action.name)
        if spec is None:
            return False, f"Unknown action: {action.name}"
        missing = set(spec.get("required", [])) - set(action.args)
        if missing:
            return False, f"{action.name} missing args: {sorted(missing)}"
        if action.name == "manipulation.publish_arm_control":
            if action.args.get("command") not in ("arm_start", "arm_pause", "system_stop"):
                return False, "command must be one of arm_start|arm_pause|system_stop"
        if action.name == "mission.terminate":
            if action.args.get("status") not in ("success", "failure", "cancelled"):
                return False, "status must be one of success|failure|cancelled"
        if action.name == "navigation.reposition":
            if action.args.get("strategy") not in ("viewpoint_left", "viewpoint_right", "step_back"):
                return False, "strategy must be one of viewpoint_left|viewpoint_right|step_back"
        return True, "ok"

    def system_prompt(self) -> str:
        """Renders the action catalogue for the policy's system prompt."""
        lines = [
            "You are the high-level decision module of a tool-delivery robot.",
            "At each step you receive one JSON observation and must reply with exactly one JSON action:",
            '{"skill": ..., "action": ..., "args": {...}, "reason": "<short reason>"}',
            "Rules: never invent skills or stations; respect confirmation requirements;",
            "stop motion and terminate with status=cancelled if the user cancels;",
            "terminate with status=failure and a clear reason when the task is unrecoverable;",
            "only terminate with status=success after the tool has been handed over.",
            "",
            "Available actions:",
        ]
        for name in self.action_names:
            spec = self._actions[name]
            args = ", ".join(f"{key}: {value}" for key, value in (spec.get("args") or {}).items())
            confirm = " [requires confirmation]" if spec.get("requires_confirmation") else ""
            lines.append(f"- {name}({args}) risk={spec.get('risk')}{confirm} :: {spec.get('effect')}")
        return "\n".join(lines)
