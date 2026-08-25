"""PPO and OPD objectives, checked numerically on CPU.

These are the parts that decide what the gradient actually says, so they are
tested directly rather than only through a training run nobody can rerun.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from training.trainers.opd import (  # noqa: E402
    MixedLossConfig,
    build_turn_weight_tensor,
    mixed_loss,
    opd_loss,
    reverse_kl_k3,
    teacher_turn_rewards,
)
from training.trainers.ppo import (  # noqa: E402
    PPOLossConfig,
    combined_loss,
    gather_logprobs,
    kl_penalty,
    masked_mean,
    policy_loss,
    shift_mask,
    value_loss,
)


def _tensors(n: int = 4):
    logprobs = torch.zeros(1, n)
    mask = torch.ones(1, n)
    advantages = torch.tensor([[1.0, -1.0, 0.5, -0.5]])
    return logprobs, mask, advantages


# ---------------------------------------------------------------------- masks


def test_masked_mean_ignores_unmasked_positions() -> None:
    values = torch.tensor([[1.0, 100.0, 3.0]])
    mask = torch.tensor([[1.0, 0.0, 1.0]])
    assert masked_mean(values, mask).item() == pytest.approx(2.0)


def test_masked_mean_is_zero_safe() -> None:
    assert masked_mean(torch.ones(1, 3), torch.zeros(1, 3)).item() == pytest.approx(0.0)


def test_shift_mask_aligns_with_gathered_logprobs() -> None:
    input_ids = torch.tensor([[5, 6, 7, 8]])
    logits = torch.zeros(1, 4, 16)
    logprobs = gather_logprobs(logits, input_ids)
    assert logprobs.shape == shift_mask(torch.ones(1, 4)).shape


# ------------------------------------------------------------------ PPO loss


def test_unchanged_policy_gives_the_plain_policy_gradient() -> None:
    logprobs, mask, advantages = _tensors()
    loss, metrics = policy_loss(logprobs, logprobs.clone(), advantages, mask)
    assert loss.item() == pytest.approx(-advantages.mean().item())
    assert metrics["policy/ratio"] == pytest.approx(1.0)
    assert metrics["policy/clip_fraction"] == pytest.approx(0.0)
    assert metrics["policy/approx_kl"] == pytest.approx(0.0, abs=1e-6)


def test_clipping_caps_the_gain_from_moving_too_far() -> None:
    old = torch.zeros(1, 2)
    advantages = torch.ones(1, 2)
    mask = torch.ones(1, 2)
    small, _ = policy_loss(old + 0.1, old, advantages, mask, clip_ratio=0.2)
    huge, metrics = policy_loss(old + 5.0, old, advantages, mask, clip_ratio=0.2)
    assert huge.item() == pytest.approx(-1.2)  # clipped at 1 + clip_ratio
    assert huge.item() < small.item()
    assert metrics["policy/clip_fraction"] == pytest.approx(1.0)


def test_negative_advantage_is_clipped_on_the_other_side() -> None:
    old = torch.zeros(1, 2)
    advantages = -torch.ones(1, 2)
    mask = torch.ones(1, 2)
    loss, _ = policy_loss(old - 5.0, old, advantages, mask, clip_ratio=0.2)
    assert loss.item() == pytest.approx(0.8)  # ratio clipped at 1 - clip_ratio


def test_tokens_outside_the_action_mask_do_not_move_the_loss() -> None:
    old = torch.zeros(1, 4)
    new = torch.tensor([[0.0, 9.0, 0.0, 9.0]])  # huge change, but masked out
    advantages = torch.tensor([[1.0, 1.0, 1.0, 1.0]])
    mask = torch.tensor([[1.0, 0.0, 1.0, 0.0]])
    loss, _ = policy_loss(new, old, advantages, mask)
    assert loss.item() == pytest.approx(-1.0)


def test_approx_kl_is_non_negative() -> None:
    old = torch.zeros(1, 3)
    mask = torch.ones(1, 3)
    for delta in (-1.0, -0.1, 0.1, 1.0):
        _, metrics = policy_loss(old + delta, old, torch.zeros(1, 3), mask)
        assert metrics["policy/approx_kl"] >= 0


def test_value_loss_is_zero_on_a_perfect_critic() -> None:
    returns = torch.tensor([[1.0, 2.0, 3.0]])
    mask = torch.ones(1, 3)
    loss, metrics = value_loss(returns.clone(), returns, returns.clone(), mask)
    assert loss.item() == pytest.approx(0.0)
    assert metrics["value/explained_variance"] == pytest.approx(1.0)


def test_value_clipping_bounds_a_large_critic_step() -> None:
    returns = torch.tensor([[10.0, 10.0]])
    old = torch.zeros(1, 2)
    mask = torch.ones(1, 2)
    loss, _ = value_loss(torch.full((1, 2), 9.0), returns, old, mask, clip=0.2)
    # The clipped branch (0.2) dominates the unclipped one (9.0).
    assert loss.item() == pytest.approx(0.5 * (10.0 - 0.2) ** 2)


def test_kl_penalty_is_zero_when_the_policies_agree() -> None:
    logprobs = torch.tensor([[-1.0, -2.0]])
    mask = torch.ones(1, 2)
    assert kl_penalty(logprobs, logprobs.clone(), mask).abs().max().item() == pytest.approx(0.0)


def test_k3_kl_penalty_is_non_negative() -> None:
    mask = torch.ones(1, 3)
    policy = torch.tensor([[-1.0, -2.0, -0.5]])
    for shift in (-2.0, -0.5, 0.5, 2.0):
        assert kl_penalty(policy, policy + shift, mask).min().item() >= -1e-6


def test_combined_loss_adds_each_configured_term() -> None:
    logprobs, mask, advantages = _tensors()
    config = PPOLossConfig(vf_coef=0.5, kl_coef=0.1, entropy_coef=0.01)
    values = torch.zeros(1, 4)
    returns = torch.ones(1, 4)
    reference = logprobs - 0.5
    entropy = torch.ones(1, 4)
    loss, metrics = combined_loss(
        logprobs, logprobs.clone(), advantages, mask, config,
        values=values, returns=returns, ref_logprobs=reference, entropy=entropy,
    )
    assert {"policy/loss", "value/loss", "policy/entropy", "policy/kl_to_reference", "loss/total"} <= set(metrics)
    assert metrics["loss/total"] == pytest.approx(loss.item())


def test_ppo_gradient_reaches_the_logprobs() -> None:
    logprobs = torch.zeros(1, 3, requires_grad=True)
    old = torch.zeros(1, 3)
    advantages = torch.tensor([[1.0, -1.0, 0.5]])
    mask = torch.ones(1, 3)
    loss, _ = policy_loss(logprobs, old, advantages, mask)
    loss.backward()
    assert logprobs.grad is not None and logprobs.grad.abs().sum() > 0


# ------------------------------------------------------------------ OPD loss


def test_reverse_kl_is_zero_exactly_when_the_models_agree() -> None:
    student = torch.tensor([[-1.0, -2.0]])
    assert reverse_kl_k3(student, student.clone()).abs().max().item() == pytest.approx(0.0)
    assert reverse_kl_k3(student, student - 1.0).min().item() > 0
    assert reverse_kl_k3(student, student + 1.0).min().item() > 0


def test_opd_loss_grows_with_disagreement() -> None:
    student = torch.zeros(1, 3)
    mask = torch.ones(1, 3)
    near, _ = opd_loss(student, student - 0.1, mask)
    far, metrics = opd_loss(student, student - 2.0, mask)
    assert far.item() > near.item()
    assert metrics["opd/disagreement"] == pytest.approx(2.0)


def test_turn_weights_scale_the_distillation_loss() -> None:
    student = torch.zeros(1, 4)
    teacher = student - 1.0
    mask = torch.ones(1, 4)
    unweighted, _ = opd_loss(student, teacher, mask)
    heavier, _ = opd_loss(student, teacher, mask, weights=torch.full((1, 4), 2.0))
    assert heavier.item() > unweighted.item()


def test_unscored_turns_contribute_nothing() -> None:
    student = torch.zeros(1, 4)
    teacher = torch.tensor([[-5.0, 0.0, -5.0, 0.0]])
    mask = torch.ones(1, 4)
    gated = torch.tensor([[0.0, 1.0, 0.0, 1.0]])  # only the agreeing turns are scored
    loss, _ = opd_loss(student, teacher, mask, weights=gated)
    assert loss.item() == pytest.approx(0.0, abs=1e-6)


def test_opd_gradient_reaches_the_student() -> None:
    student = torch.zeros(1, 3, requires_grad=True)
    loss, _ = opd_loss(student, student.detach() - 1.0, torch.ones(1, 3))
    loss.backward()
    assert student.grad is not None and student.grad.abs().sum() > 0


def test_turn_weights_land_only_on_their_own_spans() -> None:
    tensor = build_turn_weight_tensor([(1, 3), (5, 7)], [2.0, 0.0], seq_len=8)
    assert tensor.tolist() == [0.0, 2.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class _Distillation:
    def __init__(self, turn_index: int, disagreement: float, weight: float) -> None:
        self.turn_index = turn_index
        self.disagreement = disagreement
        self.weight = weight


def test_teacher_reward_is_negative_where_the_teacher_disagreed() -> None:
    rewards = teacher_turn_rewards([_Distillation(2, 1.5, 2.0)], turn_count=3)
    assert rewards == [0.0, -3.0, 0.0]


def test_teacher_reward_respects_an_explicit_position_map() -> None:
    rewards = teacher_turn_rewards([_Distillation(7, 1.0, 1.0)], turn_count=3, positions={7: 0})
    assert rewards == [-1.0, 0.0, 0.0]


def test_teacher_reward_is_positive_when_the_teacher_was_more_confident() -> None:
    assert teacher_turn_rewards([_Distillation(1, -0.5, 1.0)], turn_count=1) == [0.5]


# ------------------------------------------------------------------- the mix


def test_mix_weights_both_terms() -> None:
    ppo_term = torch.tensor(2.0)
    opd_term = torch.tensor(4.0)
    loss, metrics = mixed_loss(ppo_term, opd_term, MixedLossConfig(opd_weight=0.25, env_weight=0.5))
    assert loss.item() == pytest.approx(0.5 * 2.0 + 0.25 * 4.0)
    assert metrics["mix/opd_weight"] == 0.25 and metrics["mix/env_weight"] == 0.5


def test_pure_opd_drops_the_environment_term_entirely() -> None:
    loss, _ = mixed_loss(torch.tensor(99.0), torch.tensor(1.0), MixedLossConfig(opd_weight=1.0, env_weight=0.0))
    assert loss.item() == pytest.approx(1.0)


def test_late_training_hands_over_to_the_environment() -> None:
    loss, _ = mixed_loss(torch.tensor(1.0), torch.tensor(1.0), MixedLossConfig(opd_weight=0.1, env_weight=1.0))
    assert loss.item() == pytest.approx(1.1)
