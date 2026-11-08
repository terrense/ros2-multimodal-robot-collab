"""The chat wrapper must behave like the underlying env, including on bad output."""

from __future__ import annotations

import json

from agent_env.faults import FaultSpec, FaultType, default_params
from agent_env.l1_sim import Scenario
from training.chat_format import action_content
from training.envs import (
    MultiTurnDeliveryEnv,
    episode_from_records,
    run_episode,
    scripted_policy,
)
from training.rewards import PRESETS, compute_step_rewards


def _scenario(faults=None) -> Scenario:
    return Scenario(tool_id="wrench", target_station="station_a", operator_id="operator_001", faults=faults or [], require_confirmation=False)


def _fault(kind: FaultType, seed: int = 3) -> list[FaultSpec]:
    import random

    return [FaultSpec(kind, default_params(kind, random.Random(seed)))]


def test_reset_opens_with_system_and_first_observation() -> None:
    env = MultiTurnDeliveryEnv(_scenario(), seed=1)
    messages = env.reset()
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "Mission: deliver wrench to station_a" in messages[1]["content"]


def test_step_appends_assistant_then_next_observation() -> None:
    env = MultiTurnDeliveryEnv(_scenario(), seed=1)
    env.reset()
    messages, done, turn = env.step(action_content({"skill": "perception", "action": "verify_operator", "args": {"operator_id": "operator_001"}, "reason": "auth"}))
    assert done is False
    assert turn.invalid is False and turn.result["status"] == "succeeded"
    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert json.loads(messages[-1]["content"].split("OBSERVATION:\n")[1])["operator_verified"] is True


def test_malformed_output_becomes_a_grounding_violation_not_a_crash() -> None:
    env = MultiTurnDeliveryEnv(_scenario(), seed=1)
    env.reset()
    _, done, turn = env.step("I think I should probably go get the wrench now.")
    assert done is False
    assert turn.malformed is True and turn.invalid is True
    assert turn.reward_components["grounding_violation"] == 1.0
    assert turn.result["error_code"] == "INVALID_ACTION"


def test_illegal_skill_is_booked_as_invalid() -> None:
    env = MultiTurnDeliveryEnv(_scenario(), seed=1)
    env.reset()
    _, _, turn = env.step('{"skill":"navigation","action":"teleport","args":{"station_id":"mars"},"reason":"shortcut"}')
    assert turn.parse_status == "ok"  # parsed fine
    assert turn.invalid is True  # but is not a real skill


def test_scripted_policy_completes_a_clean_episode() -> None:
    record = run_episode(scripted_policy(), _scenario(), seed=11)
    assert record.outcome == "success"
    assert record.summary["invalid_actions"] == 0
    assert record.summary["safety_violations"] == 0
    assert 0 < len(record.turns) <= 30


def test_scripted_policy_recovers_from_a_grasp_failure() -> None:
    record = run_episode(scripted_policy(), _scenario(_fault(FaultType.GRASP_FAILURE)), seed=12)
    assert record.outcome == "success"
    assert any(turn.result["error_code"] == "GRASP_FAILED" for turn in record.turns)


def test_scripted_policy_aborts_correctly_when_the_tool_is_missing() -> None:
    record = run_episode(scripted_policy(), _scenario(_fault(FaultType.TOOL_MISSING)), seed=13)
    assert record.outcome == "correct_abort"


def test_history_truncation_keeps_the_brief_and_the_current_observation() -> None:
    env = MultiTurnDeliveryEnv(_scenario(), seed=1, max_history_turns=1)
    env.reset()
    for _ in range(3):
        env.step('{"skill":"system-state","action":"query_state","args":{},"reason":"look"}')
    messages = env.messages
    assert "Mission: deliver wrench" in messages[1]["content"]
    assert any("earlier turns elided" in message["content"] for message in messages)
    assert len(messages) < len(env.full_messages)


def test_records_roundtrip_through_the_jsonl_protocol() -> None:
    record = run_episode(scripted_policy(), _scenario(), seed=14)
    restored = episode_from_records(record.to_jsonl_records())
    assert len(restored) == 1
    assert len(restored[0].turns) == len(record.turns)
    assert restored[0].summary["outcome"] == record.outcome


def test_reward_pipeline_consumes_episode_records_directly() -> None:
    record = run_episode(scripted_policy(), _scenario(), seed=15)
    rewards = compute_step_rewards(record.steps_for_reward(), record.outcome, PRESETS["progress"])
    assert len(rewards) == len(record.turns)
    assert rewards[-1].terminal > 0
    assert sum(reward.total for reward in rewards) > 0
