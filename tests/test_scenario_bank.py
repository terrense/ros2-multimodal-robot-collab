"""Split isolation is a property to test, not a convention to remember."""

from __future__ import annotations

from pathlib import Path

import pytest

from training.data.scenario_bank import (
    BENCHMARK_COMPOSITION,
    OPERATOR_POOLS,
    build_benchmark_suite,
    check_leakage,
    fingerprint,
    load_specs,
    sample_scenarios,
    save_specs,
    split_seed,
)

BENCHMARK_FILE = Path("benchmarks/fault_injection_v1.jsonl")


def test_seed_blocks_do_not_overlap() -> None:
    seeds = {split: {split_seed(split, index) for index in range(1000)} for split in ("train", "dev", "benchmark")}
    assert not seeds["train"] & seeds["benchmark"]
    assert not seeds["train"] & seeds["dev"]
    assert not seeds["dev"] & seeds["benchmark"]


def test_operator_pools_are_disjoint() -> None:
    assert not set(OPERATOR_POOLS["train"]) & set(OPERATOR_POOLS["benchmark"])
    assert not set(OPERATOR_POOLS["dev"]) & set(OPERATOR_POOLS["benchmark"])


def test_train_and_benchmark_do_not_share_scenarios() -> None:
    report = check_leakage(sample_scenarios("train", 2000), build_benchmark_suite())
    assert report["clean"] is True
    assert report["shared_fingerprints"] == []
    assert report["shared_operators"] == []


def test_fingerprint_ignores_key_order_but_not_content() -> None:
    base = {"tool_id": "wrench", "target_station": "station_a", "operator_id": "operator_001", "require_confirmation": True, "faults": []}
    reordered = {"faults": [], "require_confirmation": True, "operator_id": "operator_001", "target_station": "station_a", "tool_id": "wrench"}
    assert fingerprint(base) == fingerprint(reordered)
    assert fingerprint(base) != fingerprint({**base, "target_station": "station_b"})
    assert fingerprint(base) != fingerprint({**base, "faults": [{"fault": "nav_timeout", "params": {"timeouts_before_success": 1}}]})


def test_fault_params_are_part_of_the_fingerprint() -> None:
    first = {"tool_id": "wrench", "target_station": "station_a", "operator_id": "o", "require_confirmation": True, "faults": [{"fault": "nav_timeout", "params": {"timeouts_before_success": 1}}]}
    second = {**first, "faults": [{"fault": "nav_timeout", "params": {"timeouts_before_success": 2}}]}
    assert fingerprint(first) != fingerprint(second)


def test_benchmark_is_deterministic_and_unique() -> None:
    first = build_benchmark_suite()
    second = build_benchmark_suite()
    assert [spec.to_dict() for spec in first] == [spec.to_dict() for spec in second]
    assert len({fingerprint(spec.scenario) for spec in first}) == len(first)
    assert len({spec.seed for spec in first}) == len(first)


def test_benchmark_composition_matches_the_declared_counts() -> None:
    specs = build_benchmark_suite()
    assert len(specs) == sum(BENCHMARK_COMPOSITION.values()) == 400
    per_fault: dict[str, int] = {}
    for spec in specs:
        faults = spec.scenario["faults"]
        key = "clean" if not faults else ("mixed" if len(faults) > 1 else faults[0]["fault"])
        per_fault[key] = per_fault.get(key, 0) + 1
    assert per_fault == BENCHMARK_COMPOSITION


def test_checked_in_benchmark_matches_the_generator() -> None:
    """Guards against an edited benchmark silently changing every reported number."""
    if not BENCHMARK_FILE.exists():
        pytest.skip("benchmark file not generated")
    on_disk = load_specs(BENCHMARK_FILE)
    expected = build_benchmark_suite()
    assert [spec.to_dict() for spec in on_disk] == [spec.to_dict() for spec in expected]


def test_specs_roundtrip_through_disk(tmp_path) -> None:
    specs = build_benchmark_suite()[:10]
    path = save_specs(tmp_path / "suite.jsonl", specs)
    assert [spec.to_dict() for spec in load_specs(path)] == [spec.to_dict() for spec in specs]


def test_spec_rebuilds_a_runnable_scenario() -> None:
    spec = next(spec for spec in build_benchmark_suite() if spec.scenario["faults"])
    scenario = spec.to_scenario()
    assert scenario.operator_id in OPERATOR_POOLS["benchmark"]
    assert len(scenario.faults) == len(spec.scenario["faults"])


def test_leakage_check_detects_a_planted_overlap() -> None:
    benchmark = build_benchmark_suite()
    report = check_leakage(benchmark[:5], benchmark)
    assert report["clean"] is False
    assert len(report["shared_fingerprints"]) == 5
