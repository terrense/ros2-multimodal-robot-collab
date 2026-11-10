"""Phase 6 acceptance tests: L1/L2 receipt parity, calibration, metrics, ablation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_env.calibration import (
    build_manifest,
    compare_family,
    wilson_interval,
)
from agent_env.faults import FaultSpec, FaultType
from agent_env.l1_sim import L1DeliveryEnv, Scenario
from agent_harness.contracts import ActionRequest, CapabilityRegistry, new_command_id
from agent_harness.runtime import FakeBackend, ToolClient
from agent_harness.runtime.l1_backend import L1Backend
from training.ablation import BudgetMismatch, render_table, require_comparable
from training.receipt_metrics import duplicate_effect_rate, receipt_metrics

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)
BENCHMARK = REPO_ROOT / "benchmarks" / "fault_injection_v1.jsonl"
MANIFEST = REPO_ROOT / "benchmarks" / "manifest.json"


def scenario(faults: list[FaultSpec] | None = None) -> Scenario:
    return Scenario(
        tool_id="hex_key_3mm",
        target_station="station_a",
        operator_id="operator_001",
        faults=faults or [],
    )


def l1_client(env: L1DeliveryEnv) -> ToolClient:
    return ToolClient(L1Backend(env), REGISTRY)


def request(capability: str, **arguments) -> ActionRequest:
    return ActionRequest(
        episode_id="ep-phase6",
        command_id=new_command_id(),
        capability=capability,
        arguments=arguments,
    )


# ---------------------------------------------------------------- P6-R1/R2


def test_l1_backend_produces_a_completed_receipt():
    env = L1DeliveryEnv(scenario(), seed=1)
    client = l1_client(env)
    try:
        req = request("system-state.query_state", requester="test")
        client.submit(req)
        receipt = client.wait(req.command_id, timeout=5)
        assert receipt.execution_status.value == "completed"
        assert receipt.call_ok is True
        assert receipt.physical_effect_verified is None
    finally:
        client.shutdown(1.0)


def test_l1_backend_rejected_action_is_rejected_not_failed():
    env = L1DeliveryEnv(scenario(), seed=1)
    client = l1_client(env)
    try:
        req = request("manipulation.publish_arm_control", command="fly")
        client.submit(req)
        receipt = client.wait(req.command_id, timeout=5)
        assert receipt.execution_status.value == "rejected"
        assert receipt.physical_effect_verified is False
    finally:
        client.shutdown(1.0)


def test_l1_and_fake_receipts_share_the_same_shape():
    env = L1DeliveryEnv(scenario(), seed=1)
    l1 = l1_client(env)
    fake = ToolClient(FakeBackend(), REGISTRY)
    try:
        l1_req = request("system-state.query_state", requester="test")
        l1.submit(l1_req)
        l1_receipt = l1.wait(l1_req.command_id, timeout=5)

        fake_req = request("system-state.query_state", requester="test")
        fake.submit(fake_req)
        fake_receipt = fake.wait(fake_req.command_id, timeout=5)

        assert set(l1_receipt.to_dict()) == set(fake_receipt.to_dict())
    finally:
        l1.shutdown(1.0)
        fake.shutdown(1.0)


# ---------------------------------------------------------------- P6-R4


def test_wilson_interval_bounds():
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert 0.0 < high < 0.4
    low, high = wilson_interval(50, 100)
    assert low < 0.5 < high


def test_calibration_refuses_small_samples():
    family = compare_family("grasp_failure", l1_rate=0.5, l2_successes=5, l2_samples=10)
    assert family["calibrated"] is False
    assert family["reason"] == "insufficient_samples"


def test_calibration_requires_the_interval_to_cover_l1():
    inside = compare_family("grasp_failure", l1_rate=0.5, l2_successes=50, l2_samples=100)
    assert inside["calibrated"] is True
    outside = compare_family("grasp_failure", l1_rate=0.95, l2_successes=50, l2_samples=100)
    assert outside["calibrated"] is False
    assert outside["reason"] == "interval_excludes_l1_rate"


def test_manifest_reports_all_calibrated():
    manifest = build_manifest(
        {
            "nav_timeout": {"l1_rate": 0.2, "l2_successes": 20, "l2_samples": 100},
            "tool_drop": {"l1_rate": 0.1, "l2_successes": 3, "l2_samples": 10},
        },
        seed=7,
        generated_at=0.0,
    )
    assert manifest["version"] == "calibration_v1"
    assert manifest["all_calibrated"] is False
    by_name = {family["name"]: family for family in manifest["families"]}
    assert by_name["nav_timeout"]["calibrated"] is True
    assert by_name["tool_drop"]["calibrated"] is False


# ---------------------------------------------------------------- P6-R5


def test_receipt_metrics_computes_uncertainty_and_latency():
    rows = [
        {
            "capability": "navigation.navigate_to_station",
            "execution_status": "completed",
            "started_at": 10.0,
            "finished_at": 10.1,
        },
        {
            "capability": "navigation.navigate_to_station",
            "execution_status": "unknown",
            "started_at": 11.0,
            "finished_at": 11.3,
        },
    ]
    metrics = receipt_metrics(rows)
    assert metrics["calls"] == 2
    assert metrics["uncertain_outcome_rate"] == 0.5
    assert metrics["latency_p50_ms"] == pytest.approx(100.0)
    assert metrics["latency_p95_ms"] == pytest.approx(300.0)


def test_duplicate_effect_rate_counts_double_dispatch():
    events = [
        {"command_id": "a", "kind": "started"},
        {"command_id": "a", "kind": "finished"},
        {"command_id": "a", "kind": "started"},
        {"command_id": "b", "kind": "started"},
    ]
    assert duplicate_effect_rate(events) == 0.5


# ---------------------------------------------------------------- P6-R8


def run_record(condition: str, **overrides) -> dict:
    record = {
        "condition": condition,
        "budgets": {"max_turns": 30, "max_tokens_chars": 4000, "max_wall_s": 120},
        "task_success": 0.5,
        "recovery_success": 0.4,
        "false_success_rate": 0.05,
        "premature_stop_rate": 0.02,
        "invalid_skill_rate": 0.01,
        "uncertain_outcome_rate": 0.03,
        "duplicate_effect_rate": 0.0,
        "actions": 12.5,
    }
    record.update(overrides)
    return record


def test_ablation_table_renders_equal_budget_runs():
    table = render_table([run_record("rules"), run_record("loop_receipts")])
    assert "| rules |" in table
    assert "| loop_receipts |" in table
    assert "Budgets: turns=30" in table
    assert "50.0%" in table


def test_ablation_refuses_mismatched_budgets():
    other = run_record("loop_receipts", budgets={"max_turns": 60, "max_tokens_chars": 4000, "max_wall_s": 120})
    with pytest.raises(BudgetMismatch, match="used budgets"):
        require_comparable([run_record("rules"), other])


# ---------------------------------------------------------------- P6-R6


def _string_values(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from _string_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _string_values(item)
    elif isinstance(node, str):
        yield node


def test_ground_truth_never_reaches_the_observation():
    for fault in FaultType:
        env = L1DeliveryEnv(scenario([FaultSpec(fault, {})]), seed=3)
        observation = env.reset().to_dict()
        values = list(_string_values(observation))
        assert fault.value not in values, f"{fault.value} leaked into the observation"
        text = json.dumps(observation, ensure_ascii=False)
        assert "_fault" not in text


# ---------------------------------------------------------------- P6-R9


def test_frozen_benchmark_is_unchanged():
    digest = hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()
    assert digest == "c703a6fc1b37a5fa4df4e2656953404d21fa334c1364da47b800a1100a5d0fdd"
    manifest_digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert manifest_digest == "a11f926f347bb4ea02fa8cb9ae0a74d47b7671d82ce0bd254cbf256598bf1233"
    assert sum(1 for _ in BENCHMARK.open(encoding="utf-8")) == 400
