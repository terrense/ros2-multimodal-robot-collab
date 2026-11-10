"""Phase 7 acceptance tests: provenance, run records, generated blocks, audit."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_env.l1_sim import L1DeliveryEnv, Scenario
from training.audit import audit
from training.counterfactual import (
    CounterfactualNotAllowed,
    UnmatchedClone,
    clone_verified,
    require_level,
    state_hash,
    verify_clone,
)
from training.envs import MultiTurnDeliveryEnv, episode_from_records
from training.generated_block import extract_block, replace_block
from training.provenance import (
    Provenance,
    ProvenanceMismatch,
    hash_text,
    mismatches,
    require_trainable,
)
from training.run_record import RunRecord, cell, load_run_records, matches

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = REPO_ROOT / "benchmarks" / "fault_injection_v1.jsonl"


def provenance() -> Provenance:
    return Provenance.build(
        model_id="rule_baseline",
        prompt_text="You are the high-level decision module.",
        action_schema={"actions": ["navigation.navigate_to_station"]},
        benchmark_path=BENCHMARK,
    )


# ---------------------------------------------------------------- P7-R1


def test_provenance_round_trip_and_hashes_are_deterministic():
    built = provenance()
    restored = Provenance.from_dict(built.to_dict())
    assert restored == built
    assert built.prompt_hash == hash_text("You are the high-level decision module.")
    assert built.benchmark_sha256.startswith("c703a6fc")


def test_v1_records_still_parse_and_v2_is_additive():
    scenario = Scenario(tool_id="hex_key_3mm", target_station="station_a", operator_id="operator_001")
    env = MultiTurnDeliveryEnv(scenario, seed=1)
    env.reset()
    env.step('{"skill": "system-state", "action": "query_state", "args": {}, "reason": "probe"}')
    v1 = env.record().to_jsonl_records()

    v2 = []
    for row in v1:
        enriched = dict(row)
        enriched["schema_version"] = 2
        enriched["provenance"] = provenance().to_dict()
        v2.append(enriched)

    first = episode_from_records(v1)
    second = episode_from_records(v2)
    assert [record.summary for record in first] == [record.summary for record in second]
    assert [[turn.to_dict() for turn in record.turns] for record in first] == [
        [turn.to_dict() for turn in record.turns] for record in second
    ]


# ---------------------------------------------------------------- P7-R3


def test_hash_drift_is_refused():
    reference = provenance()
    drifted = provenance().to_dict()
    drifted["prompt_hash"] = hash_text("a different prompt")
    assert mismatches(drifted, reference.to_dict()) == ["prompt_hash"]
    with pytest.raises(ProvenanceMismatch, match="prompt_hash"):
        require_trainable(drifted, reference.to_dict())


# ---------------------------------------------------------------- P7-R4


def test_run_record_round_trip_and_matching(tmp_path):
    record = RunRecord(
        kind="eval",
        name="rule_baseline",
        provenance=provenance().to_dict(),
        seeds=[20260724],
        budgets={"max_turns": 30, "max_tokens_chars": 4000, "max_wall_s": 120},
        artifacts=["runs/eval/rule_baseline.json"],
    )
    path = record.write(tmp_path / "rule_baseline.json")
    loaded = RunRecord.load(path)
    assert loaded.to_dict()["provenance"] == record.to_dict()["provenance"]
    assert matches(loaded, provenance())
    assert load_run_records(tmp_path)["rule_baseline"].name == "rule_baseline"


def test_cell_is_unverified_without_a_matching_record():
    value = 0.761
    assert cell(value, None, provenance()) == "unverified"
    drifted = RunRecord(
        kind="eval",
        name="rule_baseline",
        provenance={**provenance().to_dict(), "schema_hash": "deadbeefdeadbeef"},
    )
    assert cell(value, drifted, provenance()) == "unverified"
    good = RunRecord(kind="eval", name="rule_baseline", provenance=provenance().to_dict())
    assert cell(value, good, provenance()) == "76.1%"


# ---------------------------------------------------------------- P7-R5


def test_generated_block_replaces_only_its_own_section():
    document = "# Results\n\nhand-written prose\n\n<!-- BEGIN GENERATED: arms -->\nold\n<!-- END GENERATED: arms -->\n\nmore prose\n"
    updated = replace_block(document, "arms", "new table")
    assert "hand-written prose" in updated
    assert "more prose" in updated
    assert "old" not in updated
    assert extract_block(updated, "arms") == "\nnew table\n"


def test_generated_block_appends_when_missing():
    updated = replace_block("# Results\n", "arms", "table")
    assert "table" in updated
    assert extract_block(updated, "arms") == "\ntable\n"


# ---------------------------------------------------------------- P7-R7


def test_audit_flags_proxy_gap_and_repeat_exploitation():
    report = audit(
        {
            "mean_progress_reward": 0.85,
            "task_success": 0.40,
            "repeated_call_rate": 0.30,
            "false_success_rate": 0.02,
        }
    )
    assert report["proxy_gap"] == pytest.approx(0.45)
    assert "proxy_gap_high" in report["flags"]
    assert "repeat_exploitation" in report["flags"]
    assert "false_success" in report["flags"]


def test_audit_is_quiet_on_a_healthy_arm():
    report = audit({"mean_progress_reward": 0.60, "task_success": 0.58, "repeated_call_rate": 0.0})
    assert report["flags"] == []


# ---------------------------------------------------------------- P7-R8


def test_counterfactual_requires_level_and_matched_clone():
    scenario = Scenario(tool_id="hex_key_3mm", target_station="station_a", operator_id="operator_001")
    env = L1DeliveryEnv(scenario, seed=1)
    env.reset()
    clone = clone_verified(env)
    assert state_hash(clone) == state_hash(env)

    with pytest.raises(CounterfactualNotAllowed):
        require_level("l2")

    broken = clone_verified(env)
    broken.holding = "different_tool"
    with pytest.raises(UnmatchedClone):
        verify_clone(env, broken)
