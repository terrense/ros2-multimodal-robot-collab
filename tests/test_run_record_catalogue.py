"""A run with no model behind it cannot claim a prompt.

An L2 mission is dispatched straight to the stack. There is no chat surface, no
system prompt, no model. Recording it with the L1 prompt hash would be a false
record — and a false record is worse than a missing one, because the report
would then treat an L2 number as comparable with an L1 number.

These tests pin the difference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from training.provenance import environment_provenance, hash_json, hash_text
from training.run_record import build_provenance, write_run_record

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = REPO_ROOT / "benchmarks" / "fault_injection_v1.jsonl"


def test_the_l1_record_hashes_the_prompt_surface() -> None:
    provenance = environment_provenance("sft", BENCHMARK)
    assert provenance.prompt_hash != hash_text("")


def test_the_l2_record_has_no_prompt() -> None:
    """No model runs an L2 mission, so there is no prompt to hash."""
    provenance = environment_provenance("l2_ros2_stack", BENCHMARK, catalogue="ros2")
    assert provenance.prompt_hash == hash_text("")


def test_the_two_catalogues_hash_different_schemas() -> None:
    l1 = environment_provenance("x", BENCHMARK)
    l2 = environment_provenance("x", BENCHMARK, catalogue="ros2")
    assert l1.schema_hash != l2.schema_hash


def test_an_l1_record_and_an_l2_record_are_never_interchangeable() -> None:
    l1 = build_provenance("same_id", BENCHMARK).to_dict()
    l2 = build_provenance("same_id", BENCHMARK, catalogue="ros2").to_dict()
    assert l1["prompt_hash"] != l2["prompt_hash"]
    assert l1["schema_hash"] != l2["schema_hash"]


def test_the_l2_schema_hash_covers_the_whole_ros_catalogue() -> None:
    """Including the actions the agent may not call: they are still the runtime's contract."""
    from agent_harness.scripts.ros_tool_executor import SkillRegistry

    registry = SkillRegistry.load(REPO_ROOT / "skills")
    catalogue = registry.catalogue()
    assert "perception.detect_tool_yolov8" in catalogue, "non-callable actions belong in the hash"
    assert environment_provenance(
        "x", BENCHMARK, catalogue="ros2"
    ).schema_hash == hash_json(registry.catalogue())


def test_an_unknown_catalogue_is_refused() -> None:
    with pytest.raises(ValueError, match="catalogue"):
        environment_provenance("x", BENCHMARK, catalogue="l3")


def test_write_run_record_forwards_the_catalogue(tmp_path: Path) -> None:
    record = write_run_record(
        name="l2_baseline",
        kind="eval",
        model_id="l2_ros2_stack",
        benchmark=BENCHMARK,
        policy="mission-control.deliver_tool",
        episodes=5,
        catalogue="ros2",
        out=tmp_path / "l2_baseline.json",
    )
    assert record.provenance["prompt_hash"] == hash_text("")
    assert record.budgets["episodes"] == 5
