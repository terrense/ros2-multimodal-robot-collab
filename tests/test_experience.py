"""Turn credit must land on the tokens of the turn that earned it."""

from __future__ import annotations

import numpy as np

from agent_env.faults import FaultSpec, FaultType, default_params
from agent_env.l1_sim import Scenario
from tests.fakes import FakeTokenizer, UnstableTokenizer, decode
from training.config import CreditSettings
from training.envs import run_episode
from training.policies import ExpertPolicy
from training.rewards import PRESETS
from training.trainers import attach_credit, build_experience, summarize, whiten_batch


def _record(fault: FaultType | None = FaultType.GRASP_FAILURE, seed: int = 51):
    import random

    faults = [FaultSpec(fault, default_params(fault, random.Random(3)))] if fault else []
    scenario = Scenario("wrench", "station_a", "operator_001", faults, require_confirmation=False)
    return run_episode(ExpertPolicy(), scenario, seed=seed)


def _build(record, **kwargs):
    return build_experience(
        record,
        FakeTokenizer(),
        reward_config=kwargs.pop("reward_config", PRESETS["progress"]),
        credit=kwargs.pop("credit", CreditSettings()),
        **kwargs,
    )


def test_episode_mode_yields_one_sequence_with_one_span_per_turn() -> None:
    record = _record()
    experience = _build(record)
    assert experience is not None
    assert len(experience.sequences) == 1
    sequence = experience.sequences[0]
    assert len(sequence.spans) == len(record.turns) == len(experience.turns)


def test_advantages_are_zero_outside_action_spans() -> None:
    experience = _build(_record())
    sequence = experience.sequences[0]
    outside = sequence.advantages[sequence.action_mask == 0]
    assert np.all(outside == 0.0)
    assert sequence.action_mask.sum() == sum(end - start for start, end in sequence.spans)


def test_action_spans_cover_the_emitted_actions_only() -> None:
    record = _record()
    experience = _build(record)
    sequence = experience.sequences[0]
    first_span = decode(sequence.input_ids[sequence.spans[0][0] : sequence.spans[0][1]])
    assert record.turns[0].assistant_text[:20] in first_span
    assert "OBSERVATION" not in first_span


def test_step_level_credit_distinguishes_turns_that_flat_credit_cannot() -> None:
    record = _record()
    step_level = _build(record).sequences[0]
    flat = _build(record, credit=CreditSettings(terminal_broadcast=True)).sequences[0]

    step_values = {round(float(step_level.advantages[start]), 6) for start, _ in step_level.spans}
    flat_values = {round(float(flat.advantages[start]), 6) for start, _ in flat.spans}
    assert len(step_values) > 1  # different turns, different credit
    assert len(flat_values) == 1  # one episode scalar everywhere


def test_returns_equal_advantages_plus_values_after_attaching_a_critic() -> None:
    experience = _build(_record())
    values = [0.1 * index for index in range(len(experience.turns))]
    attach_credit(experience, values, CreditSettings())
    for turn, value in zip(experience.turns, values):
        assert turn.value == value
        assert turn.ret == turn.advantage + value


def test_attach_credit_rewrites_the_token_level_advantages() -> None:
    experience = _build(_record())
    before = experience.sequences[0].advantages.copy()
    attach_credit(experience, [5.0] * len(experience.turns), CreditSettings())
    after = experience.sequences[0].advantages
    assert not np.allclose(before, after)
    assert np.all(after[experience.sequences[0].action_mask == 0] == 0.0)


def test_whiten_batch_normalizes_across_episodes_not_within_one() -> None:
    experiences = [_build(_record(seed=seed)) for seed in (61, 62, 63)]
    whiten_batch(experiences)
    pooled = np.concatenate(
        [sequence.advantages[sequence.action_mask > 0] for experience in experiences for sequence in experience.sequences]
    )
    assert abs(float(pooled.mean())) < 1e-6
    assert abs(float(pooled.std()) - 1.0) < 1e-3
    for experience in experiences:
        for sequence in experience.sequences:
            assert np.all(sequence.advantages[sequence.action_mask == 0] == 0.0)


def test_turn_mode_makes_one_sequence_per_decision() -> None:
    record = _record()
    experience = _build(record, mode="turn", max_history_turns=2)
    assert len(experience.sequences) == len(record.turns)
    assert all(len(sequence.spans) == 1 for sequence in experience.sequences)


def test_turn_mode_prompts_respect_the_history_limit() -> None:
    record = _record()
    experience = _build(record, mode="turn", max_history_turns=1)
    longest = max(sequence.length for sequence in experience.sequences)
    unbounded = _build(record, mode="turn", max_history_turns=None)
    assert longest < max(sequence.length for sequence in unbounded.sequences)


def test_a_template_that_hides_turns_is_rejected() -> None:
    record = _record()
    experience = build_experience(
        record, UnstableTokenizer(), reward_config=PRESETS["progress"], credit=CreditSettings()
    )
    assert experience is None  # better no experience than misplaced credit


def test_oversized_episodes_are_dropped() -> None:
    assert _build(_record(), max_seq_len=50) is None


def test_terminal_only_reward_leaves_intermediate_turns_flat() -> None:
    experience = _build(_record(), reward_config=PRESETS["terminal_only"])
    rewards = [turn.reward for turn in experience.turns]
    assert all(reward == 0.0 for reward in rewards[:-1])
    assert rewards[-1] != 0.0


def test_summary_reports_what_was_dropped() -> None:
    experiences = [_build(_record(seed=seed)) for seed in (71, 72)]
    stats = summarize(experiences, dropped_misaligned=1, dropped_too_long=2)
    assert stats.episodes == 2 and stats.turns > 0
    assert "dropped 1 misaligned, 2 over max_seq_len" in stats.describe()
