"""Credit assignment: the right turn must get the signal, not the whole trajectory."""

from __future__ import annotations

import numpy as np

from training.rewards.credit import (
    TurnCredit,
    action_token_mask,
    assign_turn_credit,
    broadcast_to_tokens,
    compute_gae,
    discounted_returns,
    terminal_broadcast,
    whiten,
)


def test_discounted_returns_match_the_closed_form() -> None:
    returns = discounted_returns([1.0, 1.0, 1.0], gamma=0.5)
    assert np.allclose(returns, [1.75, 1.5, 1.0])


def test_gae_returns_equal_advantages_plus_values() -> None:
    rewards = [0.1, -0.2, 1.0]
    values = [0.3, 0.4, 0.5]
    advantages, returns = compute_gae(rewards, values, gamma=0.99, lam=0.95)
    assert np.allclose(returns, advantages + np.array(values))


def test_gae_with_zero_lambda_is_the_one_step_td_error() -> None:
    rewards = [0.5, 0.5]
    values = [1.0, 2.0]
    advantages, _ = compute_gae(rewards, values, gamma=1.0, lam=0.0)
    assert np.allclose(advantages, [0.5 + 2.0 - 1.0, 0.5 + 0.0 - 2.0])


def test_a_late_penalty_does_not_credit_the_turns_after_it() -> None:
    """Only turns at or before a bad step carry its (discounted) blame."""
    rewards = [0.0, 0.0, -1.0, 0.0]
    credits = assign_turn_credit(rewards, gamma=0.9)
    assert credits[3].advantage == 0.0
    assert credits[2].advantage < 0
    assert abs(credits[0].advantage) < abs(credits[2].advantage)  # discounted backwards


def test_step_level_credit_separates_a_bad_turn_from_a_good_one() -> None:
    rewards = [0.2, -1.0, 0.2]
    values = [0.0, 0.0, 0.0]
    advantages, _ = compute_gae(rewards, values, gamma=0.99, lam=0.0)
    assert advantages[1] < 0 < advantages[2]


def test_broadcast_puts_each_advantage_on_its_own_action_tokens() -> None:
    credits = [TurnCredit(0, 0.0, 0.0, 1.0, 1.0), TurnCredit(1, 0.0, 0.0, -2.0, -2.0)]
    spans = [(2, 5), (8, 10)]
    tokens = broadcast_to_tokens(credits, spans, seq_len=12)
    assert list(tokens[2:5]) == [1.0, 1.0, 1.0]
    assert list(tokens[8:10]) == [-2.0, -2.0]
    assert tokens[0] == 0.0 and tokens[6] == 0.0 and tokens[11] == 0.0  # observation tokens untouched


def test_broadcast_modes_place_the_signal_where_they_claim() -> None:
    credits = [TurnCredit(0, 0.0, 0.0, 3.0, 3.0)]
    spans = [(4, 7)]
    last = broadcast_to_tokens(credits, spans, 8, mode="last_token")
    first = broadcast_to_tokens(credits, spans, 8, mode="first_token")
    assert last[6] == 3.0 and last[4] == 0.0
    assert first[4] == 3.0 and first[6] == 0.0


def test_terminal_broadcast_cannot_tell_the_turns_apart() -> None:
    """The naive baseline the project argues against: one scalar for every token."""
    spans = [(2, 5), (8, 10)]
    flat = terminal_broadcast(-1.0, spans, 12)
    assert set(flat[2:5]) == set(flat[8:10]) == {-1.0}

    step_level = broadcast_to_tokens([TurnCredit(0, 0, 0, 0.5, 0.5), TurnCredit(1, 0, 0, -1.5, -1.5)], spans, 12)
    assert step_level[2] != step_level[8]  # step-level credit does distinguish them


def test_action_mask_covers_exactly_the_spans() -> None:
    mask = action_token_mask([(1, 3), (5, 6)], 8)
    assert list(mask) == [0, 1, 1, 0, 0, 1, 0, 0]


def test_whiten_is_stable_on_degenerate_batches() -> None:
    assert whiten(np.array([5.0])).tolist() == [0.0]
    normalized = whiten(np.array([1.0, 2.0, 3.0]))
    assert abs(float(normalized.mean())) < 1e-9


def test_spans_are_clipped_to_the_sequence() -> None:
    tokens = broadcast_to_tokens([TurnCredit(0, 0, 0, 1.0, 1.0)], [(3, 99)], seq_len=5)
    assert list(tokens) == [0.0, 0.0, 0.0, 1.0, 1.0]
