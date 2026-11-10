"""The shipped configs have to fit the card the project targets.

These are the tests that would have caught the two OOMs before the first GPU
run: a rollout budget that asks for more than the card has left, and a teacher
that is loaded once per rank.
"""

from __future__ import annotations

import pytest

from training.config import load_config
from training.memory import (
    GIB,
    ModelFootprint,
    check_rollout_budget,
    kv_bytes_per_sequence,
    plan_rollout_budget,
    training_resident_bytes,
)

CONFIG_DIR = "training/configs"

#: Qwen3-8B's published architecture, used to size the KV cache without
#: downloading the weights.
QWEN3_8B = type(
    "Qwen3_8B_Config",
    (),
    {
        "num_hidden_layers": 36,
        "hidden_size": 4096,
        "num_attention_heads": 32,
        "num_key_value_heads": 8,
    },
)()

#: The card the configs are written for.
TARGET_CARD_BYTES = 96 * GIB

PPO_CONFIGS = ("ppo_terminal.yaml", "ppo_progress.yaml")
OPD_CONFIGS = ("opd_pure.yaml", "opd_warmup_ppo.yaml")


class _Stub:
    """Accepts anything: the runner only reads a few attributes."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.__dict__.update(kwargs)


def _plan_for(stage: str, filename: str, *, gpus: int = 4):
    config = load_config(stage, f"{CONFIG_DIR}/{filename}")
    actor = ModelFootprint(params_billion=config.model.params_billion)
    reference = (
        ModelFootprint(params_billion=config.model.params_billion)
        if getattr(config, "ppo", None) and config.ppo.kl_coef
        else None
    )
    resident = training_resident_bytes(actor, gpus, reference=reference)
    return config, plan_rollout_budget(
        total_bytes=TARGET_CARD_BYTES,
        resident_bytes=resident,
        model_bytes=actor.weights_bytes,
        kv_bytes_per_sequence=kv_bytes_per_sequence(QWEN3_8B, seq_len=config.model.max_seq_len),
        concurrency=config.rollout.concurrency,
        requested_utilization=config.rollout.gpu_memory_utilization,
    )


@pytest.mark.parametrize("filename", PPO_CONFIGS)
def test_the_ppo_configs_fit_the_target_card(filename: str) -> None:
    _config, budget = _plan_for("ppo", filename)
    check_rollout_budget(budget)
    assert budget.fits


@pytest.mark.parametrize("filename", OPD_CONFIGS)
def test_the_opd_configs_fit_the_target_card(filename: str) -> None:
    _config, budget = _plan_for("opd", filename)
    check_rollout_budget(budget)
    assert budget.fits


@pytest.mark.parametrize("filename", PPO_CONFIGS + OPD_CONFIGS)
def test_no_config_asks_for_more_than_the_card_has_left(filename: str) -> None:
    """The old 0.55 and 0.45 both asked for more than a 96 GB card had left."""
    stage = "ppo" if filename in PPO_CONFIGS else "opd"
    config, budget = _plan_for(stage, filename)
    assert config.rollout.gpu_memory_utilization <= 0.45
    assert budget.requested_bytes <= budget.free_bytes


@pytest.mark.parametrize("filename", OPD_CONFIGS)
def test_the_opd_configs_ask_for_a_served_teacher(filename: str) -> None:
    config = load_config("opd", f"{CONFIG_DIR}/{filename}")
    assert config.teacher.backend == "server"
    assert config.teacher.base_url


def test_the_teacher_refuses_one_copy_per_rank(monkeypatch) -> None:
    """Four ranks each loading a 30B teacher cannot fit; the error says so."""
    from training import train_opd
    from training.policies.teacher import TeacherPlacementError

    monkeypatch.setattr(train_opd, "_world_size", lambda: 4)
    config = load_config("opd", f"{CONFIG_DIR}/opd_pure.yaml")
    config.teacher.backend = "local"

    runner = _Stub()
    runner.opd_config = config
    runner.weight_config = None
    runner.tokenizer = None

    with pytest.raises(TeacherPlacementError) as error:
        train_opd.OPDRunner.build_teacher(runner)
    message = str(error.value)
    assert "serve_teacher.sh" in message
    assert "allow_multi_rank_local" in message


def test_the_teacher_may_be_forced_to_load_locally(monkeypatch) -> None:
    from training import train_opd

    monkeypatch.setattr(train_opd, "_world_size", lambda: 4)
    monkeypatch.setattr("training.policies.teacher.spread_max_memory", lambda *a, **k: None)
    monkeypatch.setattr("training.policies.teacher.TeacherScorer", _Stub)
    config = load_config("opd", f"{CONFIG_DIR}/opd_pure.yaml")
    config.teacher.backend = "local"
    config.teacher.allow_multi_rank_local = True

    runner = _Stub()
    runner.opd_config = config
    runner.weight_config = None
    runner.tokenizer = None

    assert train_opd.OPDRunner.build_teacher(runner) is not None
