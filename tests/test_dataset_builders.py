"""SFT filtering and environment-verified DPO pairs."""

from __future__ import annotations

import random

import pytest

from agent_env.faults import FaultSpec, FaultType, default_params
from agent_env.l1_sim import L1DeliveryEnv, Scenario
from agent_env.protocol import Action, ActionRegistry
from training.chat_format import parse_action, system_prompt
from training.data.build_dpo import build_pairs, clone_env, simulate
from training.data.build_sft import episode_sample, keep, turn_samples
from training.data.corruptors import RULES, propose_all
from training.data.scenario_bank import sample_scenarios
from training.envs import run_episode, scripted_policy
from training.rewards import PRESETS


def _scenario(fault: FaultType | None = None, seed: int = 5) -> Scenario:
    faults = [FaultSpec(fault, default_params(fault, random.Random(seed)))] if fault else []
    return Scenario("wrench", "station_a", "operator_001", faults, require_confirmation=True)


# ------------------------------------------------------------------------ SFT


def test_expert_episodes_pass_the_quality_filter() -> None:
    record = run_episode(scripted_policy(), _scenario(FaultType.NAV_TIMEOUT), seed=101)
    assert keep(record) is True


def test_episodes_with_invalid_actions_are_dropped() -> None:
    from training.envs import MultiTurnDeliveryEnv

    env = MultiTurnDeliveryEnv(_scenario(), seed=102)
    env.reset()
    env.step("this is not an action")
    env.step('{"skill":"mission","action":"terminate","args":{"status":"failure","reason":"giving up"},"reason":"stop"}')
    assert keep(env.record()) is False


def test_episode_sample_supervises_every_assistant_turn() -> None:
    record = run_episode(scripted_policy(), _scenario(), seed=103)
    sample = episode_sample(record, system_prompt())
    assistants = [message for message in sample.messages if message["role"] == "assistant"]
    assert len(assistants) == len(record.turns)
    assert sample.messages[0]["role"] == "system"
    assert all(parse_action(message["content"]).action.name != "invalid.malformed_output" for message in assistants)


def test_turn_samples_end_on_the_action_and_grow_by_one_turn() -> None:
    record = run_episode(scripted_policy(), _scenario(FaultType.GRASP_FAILURE), seed=104)
    samples = turn_samples(record, system_prompt(), max_history_turns=None)
    assert len(samples) == len(record.turns)
    assert all(sample.messages[-1]["role"] == "assistant" for sample in samples)
    lengths = [len(sample.messages) for sample in samples]
    assert lengths == sorted(lengths) and lengths[0] == 3  # system + observation + action


def test_turn_samples_flag_recovery_decisions() -> None:
    record = run_episode(scripted_policy(), _scenario(FaultType.GRASP_FAILURE), seed=105)
    samples = turn_samples(record, system_prompt(), None)
    assert any(sample.meta["recovery_turn"] for sample in samples)
    assert any(sample.meta["error_code"] == "GRASP_FAILED" for sample in samples)


# ------------------------------------------------------------------------ DPO


def test_clone_env_does_not_disturb_the_original() -> None:
    env = L1DeliveryEnv(_scenario(), seed=7, registry=ActionRegistry.load())
    env.reset()
    before = env._signature()
    clone = clone_env(env)
    clone.step(Action("perception", "verify_operator", {"operator_id": "operator_001"}, "auth"))
    assert env._signature() == before
    assert env.step_index == 0
    assert env.registry is not None and clone.registry is env.registry


def test_simulate_scores_a_violation_below_the_expert_action() -> None:
    env = L1DeliveryEnv(_scenario(), seed=8, registry=ActionRegistry.load())
    env.reset()
    expert = simulate(
        env,
        Action("perception", "verify_operator", {"operator_id": "operator_001"}, "auth"),
        window=1,
        config=PRESETS["progress"],
        expert=None,
        persist=False,
    )
    unauthorized = simulate(
        env,
        Action("navigation", "navigate_to_station", {"station_id": "tool_shelf", "reason": "go"}, "skip auth"),
        window=1,
        config=PRESETS["progress"],
        expert=None,
        persist=False,
    )
    assert unauthorized.score < expert.score


def test_every_rule_declares_a_failure_mode() -> None:
    assert all(rule.tag and rule.name for rule in RULES)
    assert len({rule.name for rule in RULES}) == len(RULES)


def test_corruptors_never_propose_the_expert_action() -> None:
    from agent_env.expert_policy import ScriptedExpert
    from training.chat_format import action_content
    from training.envs import MultiTurnDeliveryEnv

    env = MultiTurnDeliveryEnv(_scenario(FaultType.GRASP_FAILURE), seed=9)
    env.reset()
    expert = ScriptedExpert()
    done = False
    while not done:
        observation = env.observation
        assert observation is not None
        action = expert.act(observation)
        for _, proposal in propose_all(observation, action):
            assert not (proposal.name == action.name and proposal.args == action.args)
        _, done, _ = env.step(action_content(action))


def test_pairs_are_verified_worse_by_the_reward_they_will_be_trained_against() -> None:
    specs = sample_scenarios("train", 25)
    pairs, stats = build_pairs(
        specs,
        granularity="step",
        window=1,
        margin=0.05,
        max_pairs_per_episode=3,
        clean_turn_prob=0.3,
        config=PRESETS["progress"],
        rng=random.Random(0),
        verbose=False,
    )
    assert pairs, "expected at least one verified pair"
    for pair in pairs:
        meta = pair["meta"]
        assert meta["score_gap"] >= 0.05
        assert meta["rejected_score"] < meta["chosen_score"]
        assert pair["prompt"][-1]["role"] == "user"
        assert pair["chosen"][0]["role"] == "assistant" and pair["rejected"][0]["role"] == "assistant"
        assert pair["chosen"][0]["content"] != pair["rejected"][0]["content"]
    assert stats["proposals_rejected_as_not_worse"], "verification should reject some proposals"


def test_trajectory_pairs_contrast_multi_step_branches() -> None:
    specs = sample_scenarios("train", 15)
    pairs, _ = build_pairs(
        specs,
        granularity="trajectory",
        window=4,
        margin=0.05,
        max_pairs_per_episode=2,
        clean_turn_prob=0.2,
        config=PRESETS["progress"],
        rng=random.Random(1),
        verbose=False,
    )
    assert pairs
    multi = [pair for pair in pairs if sum(1 for m in pair["chosen"] if m["role"] == "assistant") > 1]
    assert multi, "trajectory mode should produce multi-turn branches"


def test_blind_retry_branch_repeats_the_same_call() -> None:
    specs = sample_scenarios("train", 40)
    pairs, _ = build_pairs(
        specs,
        granularity="trajectory",
        window=4,
        margin=0.05,
        max_pairs_per_episode=3,
        clean_turn_prob=0.1,
        config=PRESETS["progress"],
        rng=random.Random(2),
        verbose=False,
    )
    retries = [pair for pair in pairs if pair["meta"]["rule"] in ("blind_retry", "stale_grasp")]
    if not retries:
        pytest.skip("no retry-family pair in this sample")
    actions = [message["content"] for message in retries[0]["rejected"] if message["role"] == "assistant"]
    assert len(set(actions)) == 1  # the loop the pair is meant to teach against
