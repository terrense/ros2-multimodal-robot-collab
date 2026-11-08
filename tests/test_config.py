"""Configs are the record of an experiment, so loading them is tested."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from training.config import (
    STAGES,
    DPOConfig,
    OPDTrainConfig,
    PPOConfig,
    SFTConfig,
    apply_overrides,
    from_dict,
    load_config,
    save_config,
    to_dict,
)

CONFIG_DIR = Path("training/configs")


def test_defaults_build_every_stage() -> None:
    assert isinstance(load_config("sft"), SFTConfig)
    assert isinstance(load_config("dpo"), DPOConfig)
    assert isinstance(load_config("ppo"), PPOConfig)
    assert isinstance(load_config("opd"), OPDTrainConfig)


def test_nested_dicts_become_nested_dataclasses() -> None:
    config = from_dict(SFTConfig, {"model": {"path": "/models/x", "max_seq_len": 4096}})
    assert config.model.path == "/models/x"
    assert config.model.max_seq_len == 4096
    assert config.optim.lr == SFTConfig().optim.lr  # untouched defaults survive


def test_unknown_keys_are_rejected_at_every_level() -> None:
    with pytest.raises(ValueError, match="unknown config keys"):
        from_dict(SFTConfig, {"epochs": 3})
    with pytest.raises(ValueError, match="unknown config keys"):
        from_dict(SFTConfig, {"model": {"learning_rate": 1}})


def test_overrides_are_typed_not_stringly() -> None:
    config = load_config("ppo", None, ["optim.lr=1e-6", "run.micro_batch_size=4", "credit.whiten_advantages=false"])
    assert isinstance(config.optim.lr, float) and config.optim.lr == 1e-6
    assert config.run.micro_batch_size == 4
    assert config.credit.whiten_advantages is False


def test_override_can_set_null() -> None:
    config = load_config("ppo", None, ["rollout.max_history_turns=null", "run.deepspeed=null"])
    assert config.rollout.max_history_turns is None
    assert config.run.deepspeed is None


def test_override_path_must_be_a_mapping() -> None:
    with pytest.raises(ValueError):
        apply_overrides({"optim": 3}, ["optim.lr=1"])
    with pytest.raises(ValueError):
        apply_overrides({}, ["missing_equals"])


def test_yaml_file_and_overrides_compose(tmp_path) -> None:
    path = tmp_path / "sft.yaml"
    path.write_text(yaml.safe_dump({"stage": "sft", "model": {"path": "/from/yaml"}, "optim": {"lr": 3e-5}}), encoding="utf-8")
    config = load_config("sft", path, ["optim.lr=7e-6"])
    assert config.model.path == "/from/yaml"
    assert config.optim.lr == 7e-6  # the override wins


def test_saved_config_reloads_identically(tmp_path) -> None:
    config = load_config("opd", None, ["teacher.path=/models/teacher", "mix.opd_end=0.05"])
    saved = save_config(config, tmp_path)
    reloaded = from_dict(OPDTrainConfig, json.loads(saved.read_text(encoding="utf-8")))
    assert to_dict(reloaded) == to_dict(config)


@pytest.mark.parametrize("path", sorted(CONFIG_DIR.glob("*.yaml")), ids=lambda path: path.name)
def test_shipped_configs_load(path: Path) -> None:
    """Every stage YAML in training/configs must still parse against the dataclasses.

    Files without a top-level `stage` are not stage configs (the experiment
    matrix, for instance) and are checked separately.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stage = raw.get("stage")
    if stage is None:
        pytest.skip(f"{path.name} is not a stage config")
    assert stage in STAGES, f"{path.name} declares unknown stage {stage!r}"
    config = load_config(stage, path)
    assert type(config) is STAGES[stage]


def test_experiment_matrix_is_well_formed() -> None:
    """Arms need a name, a metric target and a command that can be pasted verbatim."""
    matrix = yaml.safe_load((CONFIG_DIR / "experiment_matrix.yaml").read_text(encoding="utf-8"))
    assert matrix["metric"] == "recovery_success"
    names = [arm["name"] for arm in matrix["arms"]]
    assert len(names) == len(set(names))
    assert {"rule_baseline", "ppo_terminal", "ppo_progress", "opd_pure", "opd_warmup_ppo"} <= set(names)
    for arm in matrix["arms"]:
        assert 0.0 < arm["target"] < 1.0, f"{arm['name']} target must be a rate"
        assert arm["command"].startswith("python -m training.")
        assert arm["description"].strip()
    for ablation in matrix["ablations"]:
        assert ablation["question"].strip() and ablation["command"].startswith("python -m training.")


@pytest.mark.parametrize("path", sorted((CONFIG_DIR / "deepspeed").glob("*.json")), ids=lambda path: path.name)
def test_deepspeed_configs_are_valid_json_with_bf16(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["bf16"]["enabled"] is True
    assert data["zero_optimization"]["stage"] in (2, 3)
