"""Chat-native multi-turn wrapper around the L1 delivery simulator.

`L1DeliveryEnv` speaks `Action` objects; a language policy speaks text. This
wrapper is the only place that converts between the two, and it is what PPO,
OPD, evaluation and data collection all drive:

    env = MultiTurnDeliveryEnv(scenario, seed=7)
    messages = env.reset()
    while not done:
        text = policy(messages)                  # one assistant turn
        messages, done, turn = env.step(text)

Malformed policy output is not an exception: it is converted into
`MALFORMED_ACTION`, rejected by the action registry, and booked as a grounding
violation. A model that emits garbage therefore gets a learning signal instead of
crashing the rollout worker.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol, Sequence

from agent_env.l1_sim import L1DeliveryEnv, Scenario
from agent_env.protocol import Action, ActionRegistry, Observation

from ..chat_format import ParseStatus, action_content, observation_content, parse_action, system_prompt

#: A policy maps the current chat history to one assistant message.
PolicyFn = Callable[[list[dict[str, str]]], str]


class ChatEnv(Protocol):
    """Interface both the L1 sim env and the L2 ROS2 env implement."""

    def reset(self) -> list[dict[str, str]]: ...

    def step(self, assistant_text: str) -> tuple[list[dict[str, str]], bool, "TurnRecord"]: ...

    def summary(self) -> dict[str, Any]: ...


@dataclass
class TurnRecord:
    """Everything one high-level decision produced."""

    turn_index: int
    observation: dict[str, Any]
    assistant_text: str
    parse_status: str
    action: dict[str, Any]
    result: dict[str, Any]
    reward_components: dict[str, float]
    info: dict[str, Any]
    invalid: bool  # schema-illegal or unparseable => grounding violation
    malformed: bool  # unparseable specifically
    parse_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EpisodeRecord:
    episode_id: str
    scenario: dict[str, Any]
    seed: int
    source: str
    turns: list[TurnRecord] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    messages: list[dict[str, str]] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        return str(self.summary.get("outcome", "running"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "scenario": self.scenario,
            "seed": self.seed,
            "source": self.source,
            "summary": self.summary,
            "turns": [turn.to_dict() for turn in self.turns],
            "messages": self.messages,
        }

    def to_jsonl_records(self, include_messages: bool = False) -> list[dict[str, Any]]:
        """Flatten into the `agent_env.trajectory_logger` line protocol (superset)."""
        records: list[dict[str, Any]] = []
        for turn in self.turns:
            records.append(
                {
                    "record": "step",
                    "episode_id": self.episode_id,
                    "step_index": turn.turn_index,
                    "observation": turn.observation,
                    "action": turn.action,
                    "assistant_text": turn.assistant_text,
                    "parse_status": turn.parse_status,
                    "result": turn.result,
                    "reward_components": turn.reward_components,
                    "info": turn.info,
                    "phase": turn.info.get("phase"),
                    "progress": turn.info.get("progress"),
                    "invalid": turn.invalid,
                }
            )
        episode: dict[str, Any] = {
            "record": "episode",
            "episode_id": self.episode_id,
            "scenario": self.scenario,
            "summary": self.summary,
            "source": self.source,
            "seed": self.seed,
        }
        if include_messages:
            episode["messages"] = self.messages
        records.append(episode)
        return records

    def steps_for_reward(self) -> list[dict[str, Any]]:
        """Shape expected by `training.rewards.compute_step_rewards`."""
        return [
            {"action": turn.action, "reward_components": turn.reward_components, "info": turn.info}
            for turn in self.turns
        ]


class MultiTurnDeliveryEnv:
    def __init__(
        self,
        scenario: Scenario,
        seed: int = 0,
        *,
        registry: ActionRegistry | None = None,
        system: str | None = None,
        max_history_turns: int | None = None,
        max_turns: int | None = None,
        source: str = "l1_llm",
        episode_id: str | None = None,
    ) -> None:
        self.registry = registry or ActionRegistry.load()
        self.env = L1DeliveryEnv(scenario, seed=seed, registry=self.registry)
        self.scenario = scenario
        self.seed = seed
        self.source = source
        self.episode_id = episode_id or f"ep-{uuid.uuid4().hex[:12]}"
        self.system = system if system is not None else system_prompt(self.registry)
        self.max_history_turns = max_history_turns
        self.max_turns = max_turns
        self.turns: list[TurnRecord] = []
        self._observation: Observation | None = None
        self._messages: list[dict[str, str]] = []
        self._done = False

    # ------------------------------------------------------------------ loop

    def reset(self) -> list[dict[str, str]]:
        self._observation = self.env.reset()
        self.turns = []
        self._done = False
        self._messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": observation_content(self._observation, include_brief=True)},
        ]
        return self.messages

    def step(self, assistant_text: str) -> tuple[list[dict[str, str]], bool, TurnRecord]:
        if self._done:
            raise RuntimeError("episode already finished; call reset()")
        if self._observation is None:
            raise RuntimeError("call reset() before step()")

        parsed = parse_action(assistant_text)
        action = parsed.action
        legal, _ = self.registry.validate(action)
        observation_before = self._observation.to_dict()

        next_observation, reward_components, done, info = self.env.step(action)
        info = dict(info)
        info["no_progress_count"] = next_observation.no_progress_count
        info["turn"] = self.env.step_index

        turn = TurnRecord(
            turn_index=self.env.step_index,
            observation=observation_before,
            assistant_text=assistant_text,
            parse_status=parsed.status.value,
            action=action.to_dict(),
            result=self.env.last_result.to_dict(),
            reward_components=dict(reward_components),
            info=info,
            invalid=not legal,
            malformed=parsed.status is ParseStatus.MALFORMED,
            parse_error=parsed.error,
        )
        self.turns.append(turn)

        self._messages.append({"role": "assistant", "content": assistant_text})
        self._observation = next_observation
        if not done and self.max_turns is not None and len(self.turns) >= self.max_turns:
            done = True
        if not done:
            self._messages.append({"role": "user", "content": observation_content(next_observation, include_brief=False)})
        self._done = done
        return self.messages, done, turn

    # -------------------------------------------------------------- messages

    @property
    def messages(self) -> list[dict[str, str]]:
        """Chat history, optionally truncated to the last N turns for the policy."""
        if self.max_history_turns is None:
            return list(self._messages)
        head = self._messages[:2]  # system + first observation (carries the mission brief)
        tail = self._messages[2:]
        keep = self.max_history_turns * 2
        if len(tail) <= keep:
            return [*head, *tail]
        elided = (len(tail) - keep) // 2
        marker = {"role": "user", "content": f"[{elided} earlier turns elided; the observation below is current]"}
        return [*head, marker, *tail[-keep:]]

    @property
    def observation(self) -> Observation | None:
        """Current structured observation; used by scripted policies and DPO corruptors."""
        return self._observation

    @property
    def full_messages(self) -> list[dict[str, str]]:
        """Untruncated history; what PPO/OPD tokenize for episode-mode credit."""
        return list(self._messages)

    # --------------------------------------------------------------- summary

    def summary(self) -> dict[str, Any]:
        data = self.env.episode_summary()
        data["invalid_actions"] = sum(1 for turn in self.turns if turn.invalid)
        data["malformed_actions"] = sum(1 for turn in self.turns if turn.malformed)
        data["repaired_actions"] = sum(1 for turn in self.turns if turn.parse_status == ParseStatus.REPAIRED.value)
        data["turns"] = len(self.turns)
        return data

    def record(self) -> EpisodeRecord:
        return EpisodeRecord(
            episode_id=self.episode_id,
            scenario=self.scenario.to_dict(),
            seed=self.seed,
            source=self.source,
            turns=list(self.turns),
            summary=self.summary(),
            messages=self.full_messages,
        )


def run_episode(
    policy: PolicyFn,
    scenario: Scenario,
    seed: int = 0,
    *,
    registry: ActionRegistry | None = None,
    max_history_turns: int | None = None,
    max_turns: int | None = None,
    source: str = "l1_llm",
    episode_id: str | None = None,
    system: str | None = None,
) -> EpisodeRecord:
    """Drive one episode to termination with a text policy."""
    env = MultiTurnDeliveryEnv(
        scenario,
        seed=seed,
        registry=registry,
        system=system,
        max_history_turns=max_history_turns,
        max_turns=max_turns,
        source=source,
        episode_id=episode_id,
    )
    messages = env.reset()
    done = False
    while not done:
        text = policy(messages)
        messages, done, _ = env.step(text)
    return env.record()


def scripted_policy() -> PolicyFn:
    """Adapter turning the rule-based expert into a text policy (baseline / smoke tests).

    It reads the observation back out of the chat history, so it exercises exactly
    the same prompt surface an LLM policy sees.
    """
    from agent_env.expert_policy import ScriptedExpert

    state: dict[str, ScriptedExpert] = {}

    def policy(messages: list[dict[str, str]]) -> str:
        observation = _observation_from_messages(messages)
        if observation.step_index == 0 or "expert" not in state:
            state["expert"] = ScriptedExpert()  # one expert instance per episode
        return action_content(state["expert"].act(observation))

    return policy


def _observation_from_messages(messages: Sequence[dict[str, str]]) -> Observation:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        marker = content.find("{")
        if marker == -1:
            continue
        return _observation_from_dict(json.loads(content[marker:]))
    raise ValueError("no observation found in chat history")


def _observation_from_dict(payload: dict[str, Any]) -> Observation:
    from agent_env.protocol import Detection, StepResult

    result = payload.get("last_result") or {}
    return Observation(
        step_index=int(payload.get("step_index", 0)),
        task=dict(payload.get("task") or {}),
        robot_location=str(payload.get("robot_location", "")),
        holding_tool=payload.get("holding_tool"),
        operator_verified=bool(payload.get("operator_verified", False)),
        detections=[Detection(**detection) for detection in payload.get("detections") or []],
        last_action=payload.get("last_action"),
        last_result=StepResult(
            status=str(result.get("status", "none")),
            error_code=str(result.get("error_code", "OK")),
            message=str(result.get("message", "")),
            data=dict(result.get("data") or {}),
        ),
        safety_events=list(payload.get("safety_events") or []),
        no_progress_count=int(payload.get("no_progress_count", 0)),
        steps_remaining=int(payload.get("steps_remaining", 0)),
    )


def episode_from_records(records: Sequence[dict[str, Any]]) -> list[EpisodeRecord]:
    """Rebuild `EpisodeRecord`s from a rollout JSONL (steps then episode line)."""
    by_episode: dict[str, list[dict[str, Any]]] = {}
    closers: dict[str, dict[str, Any]] = {}
    for record in records:
        episode_id = str(record.get("episode_id"))
        if record.get("record") == "step":
            by_episode.setdefault(episode_id, []).append(record)
        elif record.get("record") == "episode":
            closers[episode_id] = record

    episodes: list[EpisodeRecord] = []
    for episode_id, closer in closers.items():
        steps = sorted(by_episode.get(episode_id, []), key=lambda item: item.get("step_index", 0))
        turns = [
            TurnRecord(
                turn_index=int(step.get("step_index", index + 1)),
                observation=step.get("observation") or {},
                assistant_text=step.get("assistant_text", ""),
                parse_status=step.get("parse_status", ParseStatus.OK.value),
                action=step.get("action") or {},
                result=step.get("result") or {},
                reward_components=step.get("reward_components") or {},
                info=step.get("info") or {"phase": step.get("phase"), "progress": step.get("progress")},
                invalid=bool(step.get("invalid", False)),
                malformed=step.get("parse_status") == ParseStatus.MALFORMED.value,
            )
            for index, step in enumerate(steps)
        ]
        episodes.append(
            EpisodeRecord(
                episode_id=episode_id,
                scenario=closer.get("scenario") or {},
                seed=int(closer.get("seed", 0)),
                source=str(closer.get("source", "unknown")),
                turns=turns,
                summary=closer.get("summary") or {},
                messages=closer.get("messages") or [],
            )
        )
    return episodes


__all__ = [
    "ChatEnv",
    "EpisodeRecord",
    "MultiTurnDeliveryEnv",
    "PolicyFn",
    "TurnRecord",
    "episode_from_records",
    "run_episode",
    "scripted_policy",
]
