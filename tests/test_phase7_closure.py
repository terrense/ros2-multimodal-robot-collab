"""The Phase 7 acceptance criteria the merge left open.

O7 said four things that were not actually built:

    P7-R1  v2 provenance is additive; v1 records still parse
    P7-R2  dataset builders produce identical output for v1 inputs
    P7-R3  mismatched prompt/schema hashes are refused by the loader
    P7-R8  a counterfactual is refused without a verified clone hash
    P7-R9  no physical-robot claim is representable

Each test below states the requirement and what would fail without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_env.l1_sim import L1DeliveryEnv, Scenario
from training.counterfactual import UnmatchedClone
from training.data.build_dpo import clone_env
from training.data.build_sft import episode_sample, turn_samples
from training.datasets import read_jsonl
from training.envs import MultiTurnDeliveryEnv, episode_from_records
from training.provenance import (
    Provenance,
    ProvenanceMismatch,
    environment_provenance,
    require_trainable_records,
)
from training.report import load_matrix

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = REPO_ROOT / "benchmarks" / "fault_injection_v1.jsonl"


def _scenario() -> Scenario:
    return Scenario(tool_id="hex_key_3mm", target_station="station_a", operator_id="operator_001")


def _provenance() -> Provenance:
    return environment_provenance("rule_baseline", BENCHMARK)


def _one_step_env(provenance: Provenance | None = None) -> MultiTurnDeliveryEnv:
    env = MultiTurnDeliveryEnv(
        _scenario(), seed=1, provenance=provenance, episode_id="ep-fixed"
    )
    env.reset()
    env.step('{"skill": "system-state", "action": "query_state", "args": {}, "reason": "probe"}')
    return env


# ---------------------------------------------------------------- P7-R1


def test_a_v1_record_carries_no_extra_keys() -> None:
    """v1 is not "v2 minus something": it is exactly what it always was."""
    records = _one_step_env().record().to_jsonl_records()
    for row in records:
        assert "schema_version" not in row
        assert "provenance" not in row
        assert "receipt" not in row


def test_a_v2_record_adds_provenance_and_a_receipt() -> None:
    records = _one_step_env(_provenance()).record().to_jsonl_records()
    steps = [row for row in records if row["record"] == "step"]
    episode = [row for row in records if row["record"] == "episode"][0]
    assert steps and episode
    for row in records:
        assert row["schema_version"] == 2
        assert row["provenance"]["prompt_hash"]
    assert steps[0]["receipt"]["execution_status"] in {"completed", "failed"}
    assert "physical_effect_verified" not in steps[0]["receipt"]


def test_a_v2_record_still_parses_into_the_same_episode() -> None:
    v1 = _one_step_env().record().to_jsonl_records()
    v2 = _one_step_env(_provenance()).record().to_jsonl_records()
    first = episode_from_records(v1)
    second = episode_from_records(v2)
    assert [record.summary for record in first] == [record.summary for record in second]
    assert [[turn.to_dict() for turn in record.turns] for record in first] == [
        [turn.to_dict() for turn in record.turns] for record in second
    ]
    # ... and the v2 one carries its provenance through the round trip.
    assert second[0].provenance is not None
    assert first[0].provenance is None


# ---------------------------------------------------------------- P7-R2


def test_a_v1_corpus_produces_identical_samples() -> None:
    """The builder must not change what a v1 corpus contains."""
    v1 = _one_step_env().record()
    v2 = _one_step_env(_provenance()).record()
    system = "system prompt"

    left = episode_sample(v1, system)
    right = episode_sample(v2, system)
    assert "provenance" not in left.meta
    assert right.meta["provenance"]["prompt_hash"]

    # Same content: the v2 support adds the provenance and changes nothing else.
    assert {key: value for key, value in right.meta.items() if key != "provenance"} == left.meta

    # The turn granularity behaves the same way.
    turn_left = turn_samples(v1, system, None)[0]
    turn_right = turn_samples(v2, system, None)[0]
    assert "provenance" not in turn_left.meta
    assert {key: value for key, value in turn_right.meta.items() if key != "provenance"} == turn_left.meta


# ---------------------------------------------------------------- P7-R3


def test_the_loader_refuses_a_sample_measured_under_other_rules() -> None:
    expected = _provenance()
    drifted = expected.to_dict()
    drifted["prompt_hash"] = "0000000000000000"
    rows = [{"messages": [], "meta": {"provenance": drifted}}]
    with pytest.raises(ProvenanceMismatch, match="prompt_hash"):
        require_trainable_records(rows, expected)


def test_the_loader_accepts_v1_samples_unchanged() -> None:
    rows = [{"messages": [], "meta": {"episode_id": "ep-1"}}]
    assert require_trainable_records(rows, _provenance()) == 0


def test_read_jsonl_refuses_a_drifted_corpus(tmp_path: Path) -> None:
    expected = _provenance()
    drifted = expected.to_dict()
    drifted["schema_hash"] = "1111111111111111"
    path = tmp_path / "train.jsonl"
    path.write_text(
        json.dumps({"messages": [], "meta": {"provenance": drifted}}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ProvenanceMismatch, match="schema_hash"):
        read_jsonl(path, expected_provenance=expected)


def test_read_jsonl_accepts_a_matching_corpus(tmp_path: Path) -> None:
    expected = _provenance()
    path = tmp_path / "train.jsonl"
    path.write_text(
        json.dumps({"messages": [], "meta": {"provenance": expected.to_dict()}}) + "\n",
        encoding="utf-8",
    )
    rows = read_jsonl(path, expected_provenance=expected)
    assert len(rows) == 1


# ---------------------------------------------------------------- P7-R8


def test_the_dpo_builder_verifies_its_clone() -> None:
    """A clone that does not reproduce the state must not be scored."""
    env = L1DeliveryEnv(_scenario(), seed=3)
    env.reset()
    clone = clone_env(env)
    assert clone is not env

    # A clone whose state drifted is refused, not silently used.
    broken = clone_env(env)
    broken.step_index = broken.step_index + 1
    with pytest.raises(UnmatchedClone):
        from training.counterfactual import verify_clone

        verify_clone(env, broken)


# ---------------------------------------------------------------- P7-R9


def test_the_report_refuses_a_physical_claim_field(tmp_path: Path) -> None:
    path = tmp_path / "matrix.yaml"
    path.write_text(
        "metric: recovery_success\n"
        "benchmark: benchmarks/fault_injection_v1.jsonl\n"
        "arms:\n"
        "  - name: rule_baseline\n"
        "    target: 0.718\n"
        "    physical_robot_success: 0.9\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="physical_robot_success"):
        load_matrix(path)


def test_the_shipped_matrix_has_no_physical_claim() -> None:
    matrix = load_matrix(REPO_ROOT / "training" / "configs" / "experiment_matrix.yaml")
    assert matrix["arms"]
