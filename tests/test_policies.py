"""Baselines, the teacher gate and the OPD -> PPO schedule.

The gate and the schedule are the parts of OPD that decide where the teacher
budget goes and when the environment takes over, so they are tested without a
GPU; only the scorer itself needs one.
"""

from __future__ import annotations

import random

import pytest

from agent_env.faults import FaultSpec, FaultType, default_params
from agent_env.l1_sim import Scenario
from training.policies import (
    ExpertPolicy,
    GateConfig,
    MixSchedule,
    OPDConfig,
    RuleBaselinePolicy,
    RuleCoverage,
    TurnFeatures,
    TurnGate,
    distillation_weight,
    features_from_turn,
    load_policy,
)
from training.policies.base import BasePolicy, GenerationConfig
from training.policies.llm_policy import REPAIR_HINT, _RepairMixin, render_prompt
from training.envs import run_episode


def _scenario(fault: FaultType | None = None, seed: int = 3) -> Scenario:
    faults = [FaultSpec(fault, default_params(fault, random.Random(seed)))] if fault else []
    return Scenario("wrench", "station_a", "operator_001", faults, require_confirmation=True)


# -------------------------------------------------------------------- gating


def _features(**overrides) -> TurnFeatures:
    base = {"turn_index": 1, "error_code": "OK", "result_status": "succeeded"}
    return TurnFeatures(**{**base, **overrides})


def test_routine_turns_do_not_spend_teacher_budget() -> None:
    gate = TurnGate(GateConfig(random_sample_prob=0.0))
    decision = gate.decide(_features())
    assert decision.scored is False and decision.reason == "routine"
    assert gate.scored == 0


def test_error_and_safety_turns_are_scored() -> None:
    gate = TurnGate(GateConfig(random_sample_prob=0.0, max_scored_fraction=1.0))
    assert gate.decide(_features(error_code="GRASP_FAILED")).reason == "error"
    assert gate.decide(_features(result_status="rejected")).reason == "error"
    assert gate.decide(_features(safety_events=["user_cancel_requested"])).reason == "safety_event"


def test_no_progress_and_uncertainty_trigger_scoring() -> None:
    gate = TurnGate(GateConfig(random_sample_prob=0.0, max_scored_fraction=1.0))
    assert gate.decide(_features(no_progress_count=2)).reason == "no_progress"
    assert gate.decide(_features(student_entropy=1.5)).reason == "uncertain"
    assert gate.decide(_features(prev_disagreement=0.9)).reason == "prior_disagreement"


def test_budget_cap_stops_scoring_even_on_errors() -> None:
    gate = TurnGate(GateConfig(random_sample_prob=0.0, max_scored_turns=2, max_scored_fraction=1.0))
    reasons = [gate.decide(_features(error_code="TIMEOUT")).scored for _ in range(5)]
    assert reasons == [True, True, False, False, False]


def test_fraction_budget_scales_with_episode_length() -> None:
    gate = TurnGate(GateConfig(random_sample_prob=0.0, max_scored_fraction=0.5))
    scored = [gate.decide(_features(error_code="TIMEOUT")).scored for _ in range(10)]
    assert 4 <= sum(scored) <= 6  # about half the turns, not all of them


def test_random_sampling_keeps_an_unbiased_slice() -> None:
    gate = TurnGate(GateConfig(random_sample_prob=1.0, max_scored_fraction=1.0), rng=random.Random(0))
    assert gate.decide(_features()).reason == "random_sample"


def test_features_are_built_from_a_turn_record() -> None:
    record = run_episode(ExpertPolicy(), _scenario(FaultType.GRASP_FAILURE), seed=31)
    failing = next(turn for turn in record.turns if turn.result["error_code"] == "GRASP_FAILED")
    following = record.turns[record.turns.index(failing) + 1]
    features = features_from_turn(following)
    assert features.error_code == "GRASP_FAILED"  # the observation the next decision reacts to


# ------------------------------------------------------------------ weighting


def test_weight_grows_with_uncertainty_and_disagreement() -> None:
    config = OPDConfig()
    base = distillation_weight(0.0, 0.0, config)
    assert distillation_weight(1.0, 0.0, config) > base
    assert distillation_weight(0.0, 1.0, config) > base
    assert distillation_weight(1.0, 1.0, config) > distillation_weight(1.0, 0.0, config)


def test_weight_is_clipped_and_ignores_negative_disagreement() -> None:
    config = OPDConfig(max_weight=2.0)
    assert distillation_weight(10.0, 10.0, config) == 2.0
    # Teacher more confident than the student is not a reason to push harder.
    assert distillation_weight(0.0, -5.0, config) == distillation_weight(0.0, 0.0, config)


# ------------------------------------------------------------------ schedule


def test_warmup_holds_teacher_guidance_at_full_strength() -> None:
    schedule = MixSchedule(total_steps=100, warmup_fraction=0.2)
    assert schedule.opd_weight(0) == pytest.approx(1.0)
    assert schedule.opd_weight(20) == pytest.approx(1.0)
    assert schedule.opd_weight(21) < 1.0


def test_teacher_hands_over_to_the_environment() -> None:
    schedule = MixSchedule(total_steps=100, warmup_fraction=0.0)
    opd = [schedule.opd_weight(step) for step in range(0, 101, 10)]
    env = [schedule.env_weight(step) for step in range(0, 101, 10)]
    assert opd == sorted(opd, reverse=True)
    assert env == sorted(env)
    assert schedule.opd_weight(100) == pytest.approx(0.1)
    assert schedule.env_weight(100) == pytest.approx(1.0)


def test_schedule_modes_all_reach_the_endpoints() -> None:
    for mode in ("cosine", "linear", "step"):
        schedule = MixSchedule(total_steps=50, warmup_fraction=0.0, mode=mode)
        assert schedule.opd_weight(0) == pytest.approx(1.0)
        assert schedule.opd_weight(50) == pytest.approx(0.1)


# ------------------------------------------------------------------ baselines


def test_expert_solves_a_dropped_tool_but_the_deployed_rules_do_not() -> None:
    """The coverage gap post-training is supposed to close, stated as a test."""
    scenario = _scenario(FaultType.TOOL_DROP, seed=4)
    expert = run_episode(ExpertPolicy(), scenario, seed=41)
    baseline = run_episode(RuleBaselinePolicy(), scenario, seed=41)
    assert expert.outcome == "success"
    assert baseline.outcome != "success"


def test_full_coverage_closes_the_gap() -> None:
    scenario = _scenario(FaultType.TOOL_DROP, seed=4)
    record = run_episode(RuleBaselinePolicy(RuleCoverage.full()), scenario, seed=41)
    assert record.outcome == "success"


def test_rule_baseline_emits_only_legal_actions() -> None:
    for fault in FaultType:
        record = run_episode(RuleBaselinePolicy(), _scenario(fault, seed=6), seed=42)
        assert record.summary["invalid_actions"] == 0
        assert record.summary["malformed_actions"] == 0


def test_rule_baseline_never_moves_before_authorization() -> None:
    record = run_episode(RuleBaselinePolicy(), _scenario(FaultType.IDENTITY_MISMATCH, seed=8), seed=43)
    assert record.summary["safety_violations"] == 0


def test_coverage_flags_are_independent() -> None:
    coverage = RuleCoverage().without(reposition_on_detection_failure=False)
    assert coverage.reposition_on_detection_failure is False
    assert coverage.redetect_after_grasp_failure is True


# -------------------------------------------------------------------- loading


def test_load_policy_dispatches_scripted_specs() -> None:
    assert isinstance(load_policy("expert"), ExpertPolicy)
    assert load_policy("rules").coverage.recover_dropped_tool is False
    assert load_policy("rules:full").coverage.recover_dropped_tool is True


def test_load_policy_rejects_unknown_or_incomplete_specs() -> None:
    with pytest.raises(ValueError):
        load_policy("vllm")
    with pytest.raises(ValueError):
        load_policy("tensorrt:/models/x")


# --------------------------------------------------------------------- repair


class _ScriptedBackend(_RepairMixin, BasePolicy):
    """Emits garbage first, valid JSON once the repair hint appears."""

    name = "scripted_backend"

    def __init__(self, repair_attempts: int) -> None:
        self.generation = GenerationConfig()
        self.repair_attempts = repair_attempts
        self.calls: list[int] = []

    def _generate(self, batch):
        self.calls.append(len(batch))
        return [
            '{"skill":"system-state","action":"query_state","args":{}}'
            if any(REPAIR_HINT in message["content"] for message in messages)
            else "I will pick up the wrench."
            for messages in batch
        ]


def test_repair_is_off_by_default_so_malformed_rates_stay_measurable() -> None:
    policy = _ScriptedBackend(repair_attempts=0)
    assert policy.act([{"role": "user", "content": "obs"}]) == "I will pick up the wrench."
    assert policy.calls == [1]


def test_repair_reasks_only_the_broken_rows() -> None:
    policy = _ScriptedBackend(repair_attempts=1)
    outputs = policy.act_batch([[{"role": "user", "content": "obs"}] for _ in range(3)])
    assert all("query_state" in text for text in outputs)
    assert policy.calls == [3, 3]


def test_render_prompt_appends_the_generation_prompt() -> None:
    class Tokenizer:
        chat_template = "{% for m in messages %}{{ m.content }}{% endfor %}"

        def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=False, **kwargs):
            text = "".join(f"<{m['role']}>{m['content']}" for m in messages)
            return text + "<assistant>" if add_generation_prompt else text

    prompt = render_prompt(Tokenizer(), [{"role": "user", "content": "obs"}])
    assert prompt.endswith("<assistant>")
