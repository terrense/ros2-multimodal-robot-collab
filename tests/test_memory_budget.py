"""The memory arithmetic that keeps a run from dying in an OOM.

Three models share one card in the colocated layout: the actor under ZeRO-3,
a full copy of the reference model, and the in-process rollout engine. The
engine's budget is a fraction of the *whole* card, so the arithmetic has to be
right before the run starts rather than discovered at hour one.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from training.memory import (
    GIB,
    InsufficientRolloutMemory,
    ModelFootprint,
    check_rollout_budget,
    kv_bytes_per_sequence,
    plan_rollout_budget,
    training_resident_bytes,
    zero3_shard_bytes,
)

ACTOR = ModelFootprint(params_billion=8.2)
REFERENCE = ModelFootprint(params_billion=8.2)


@dataclass
class _FakeModelConfig:
    num_hidden_layers: int = 36
    hidden_size: int = 4096
    num_attention_heads: int = 32
    num_key_value_heads: int = 8


def test_zero3_splits_the_actor_across_the_ranks() -> None:
    assert zero3_shard_bytes(100, 4) == 25
    assert zero3_shard_bytes(100, 1) == 100


def test_the_reference_model_is_not_partitioned() -> None:
    """Every rank carries a whole reference copy -- that is the surprise."""
    resident = training_resident_bytes(ACTOR, 4, reference=REFERENCE)
    without_reference = training_resident_bytes(ACTOR, 4)
    assert resident - without_reference == REFERENCE.weights_bytes
    assert resident > without_reference


def test_the_kv_cache_follows_the_model_config() -> None:
    # 2 (K and V) * 8 kv heads * 128 head dim * 2 bytes * 36 layers = 147456
    per_token = 2 * 8 * 128 * 2 * 36
    assert kv_bytes_per_sequence(_FakeModelConfig(), seq_len=8192) == per_token * 8192


def test_a_budget_that_fits_is_returned() -> None:
    budget = plan_rollout_budget(
        total_bytes=96 * GIB,
        resident_bytes=45 * GIB,
        model_bytes=16 * GIB,
        kv_bytes_per_sequence=1 * GIB,
        concurrency=16,
        requested_utilization=0.42,
    )
    assert budget.fits
    assert check_rollout_budget(budget) is budget


def test_a_budget_that_does_not_fit_is_refused_with_the_number_that_does() -> None:
    budget = plan_rollout_budget(
        total_bytes=96 * GIB,
        resident_bytes=45 * GIB,
        model_bytes=16 * GIB,
        kv_bytes_per_sequence=1 * GIB,
        concurrency=16,
        requested_utilization=0.55,
    )
    assert not budget.fits
    with pytest.raises(InsufficientRolloutMemory) as error:
        check_rollout_budget(budget)
    message = str(error.value)
    assert "gpu_memory_utilization" in message
    # The message has to name a number the operator can paste in.
    assert f"{budget.max_utilization:.2f}" in message


def test_the_budget_reports_the_concurrency_the_kv_cache_can_hold() -> None:
    budget = plan_rollout_budget(
        total_bytes=96 * GIB,
        resident_bytes=45 * GIB,
        model_bytes=16 * GIB,
        kv_bytes_per_sequence=2 * GIB,
        concurrency=32,
        requested_utilization=0.42,
    )
    # 0.42 of 96 GiB is 40.3 GiB; 16 GiB of weights leave 24 GiB of cache.
    assert budget.max_concurrency == 12
    assert not budget.holds_concurrency
    assert "12" in budget.concurrency_note()


def test_the_engine_needs_room_for_at_least_one_sequence() -> None:
    budget = plan_rollout_budget(
        total_bytes=96 * GIB,
        resident_bytes=60 * GIB,
        model_bytes=28 * GIB,
        kv_bytes_per_sequence=4 * GIB,
        concurrency=4,
        requested_utilization=0.30,
    )
    assert budget.fits  # the engine's share exists
    assert budget.max_concurrency == 0
    with pytest.raises(InsufficientRolloutMemory) as error:
        check_rollout_budget(budget)
    assert "no room for a KV cache" in str(error.value)


def test_the_summary_names_the_cards_and_the_shares() -> None:
    budget = plan_rollout_budget(
        total_bytes=96 * GIB,
        resident_bytes=45 * GIB,
        model_bytes=16 * GIB,
        kv_bytes_per_sequence=1 * GIB,
        concurrency=16,
        requested_utilization=0.42,
    )
    summary = budget.summary()
    assert "96.0 GiB" in summary
    assert "0.42" in summary
