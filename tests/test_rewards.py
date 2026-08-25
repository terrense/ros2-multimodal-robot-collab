"""The reward presets are the ablation surface, so their differences are tested."""

from __future__ import annotations

from training.rewards import PRESETS, RewardConfig, compute_step_rewards, episode_reward


def _step(progress=0.0, grounding=0.0, safety=0.0, stuck=0, action=None):
    return {
        "action": action or {"skill": "system-state", "action": "query_state", "args": {}, "reason": "look"},
        "reward_components": {
            "progress_delta": progress,
            "grounding_violation": grounding,
            "safety_violation": safety,
            "step_cost": 1.0,
            "terminal_success": 0.0,
        },
        "info": {"no_progress_count": stuck},
    }


def test_terminal_only_ignores_every_dense_component() -> None:
    steps = [_step(progress=0.2), _step(grounding=1.0), _step(safety=1.0)]
    rewards = compute_step_rewards(steps, "success", PRESETS["terminal_only"])
    assert [reward.total for reward in rewards[:-1]] == [0.0, 0.0]
    assert rewards[-1].total == 1.0  # only the outcome survives


def test_progress_preset_rewards_subgoals_before_the_end() -> None:
    steps = [_step(progress=0.18), _step(progress=0.24)]
    rewards = compute_step_rewards(steps, "success", PRESETS["progress"])
    assert rewards[0].progress > 0
    assert rewards[0].total > 0  # signal arrives at the sub-goal, not only at the end


def test_regression_is_penalized_at_a_lower_weight() -> None:
    config = PRESETS["progress"]
    forward = compute_step_rewards([_step(progress=0.2)], "running", config)[0]
    backward = compute_step_rewards([_step(progress=-0.2)], "running", config)[0]
    assert backward.progress < 0
    assert abs(backward.progress) < abs(forward.progress)


def test_violations_are_negative_and_safety_outweighs_grounding() -> None:
    grounding = compute_step_rewards([_step(grounding=1.0)], "running")[0]
    safety = compute_step_rewards([_step(safety=1.0)], "running")[0]
    assert grounding.grounding < 0 and safety.safety < 0
    assert safety.total < grounding.total


def test_repeated_identical_call_is_penalized_once_per_repeat() -> None:
    call = {"skill": "manipulation", "action": "pick_tool", "args": {"tool_id": "wrench", "grasp_id": "g1"}, "reason": "retry"}
    other = {**call, "reason": "different wording only"}
    rewards = compute_step_rewards([_step(action=call), _step(action=other)], "running")
    assert rewards[0].repeat == 0.0
    assert rewards[1].repeat < 0.0  # `reason` text must not disguise an identical call


def test_no_progress_penalty_needs_to_cross_the_threshold() -> None:
    """Two stalled turns can be the injected fault; three is the policy looping."""
    config = PRESETS["progress"]
    assert compute_step_rewards([_step(stuck=1)], "running", config)[0].no_progress == 0.0
    assert compute_step_rewards([_step(stuck=2)], "running", config)[0].no_progress == 0.0
    assert compute_step_rewards([_step(stuck=3)], "running", config)[0].no_progress < 0.0


def test_terminal_values_separate_correct_abort_from_wrong_termination() -> None:
    steps = [_step()]
    assert episode_reward(steps, "correct_abort") > 0
    assert episode_reward(steps, "wrong_termination") < 0
    assert episode_reward(steps, "success") > episode_reward(steps, "correct_abort")


def test_ablation_presets_drop_exactly_one_component() -> None:
    steps = [_step(progress=0.2, grounding=1.0, safety=1.0, stuck=3)]
    full = compute_step_rewards(steps, "success", PRESETS["progress"])[0]
    assert compute_step_rewards(steps, "success", PRESETS["progress_no_grounding"])[0].grounding == 0.0
    assert compute_step_rewards(steps, "success", PRESETS["progress_no_safety"])[0].safety == 0.0
    assert compute_step_rewards(steps, "success", PRESETS["progress_no_terminal"])[0].terminal == 0.0
    assert full.grounding < 0 and full.safety < 0 and full.terminal > 0


def test_config_roundtrips_through_dict_with_preset_base() -> None:
    config = RewardConfig.from_dict({"preset": "progress", "w_safety": 2.0})
    assert config.w_safety == 2.0 and config.w_progress == PRESETS["progress"].w_progress
    assert RewardConfig.from_dict(config.to_dict()).w_safety == 2.0


def test_rewards_are_clipped() -> None:
    config = PRESETS["progress"].with_overrides(clip=(-0.1, 0.1))
    assert compute_step_rewards([_step(safety=1.0)], "wrong_termination", config)[0].total == -0.1
