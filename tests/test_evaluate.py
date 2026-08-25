"""The failure taxonomy has to tell the failure modes apart, not just count them."""

from __future__ import annotations

import random

from agent_env.faults import FaultSpec, FaultType, default_params
from agent_env.l1_sim import Scenario
from training.envs import MultiTurnDeliveryEnv, run_episode
from training.evaluate import (
    NO_PROGRESS_THRESHOLD,
    aggregate,
    behaviour_flags,
    bucketed,
    episode_metrics,
    evaluate_records,
    markdown_table,
    terminal_class,
)
from training.policies import ExpertPolicy, RuleBaselinePolicy

TERMINATE_SUCCESS = '{"skill":"mission","action":"terminate","args":{"status":"success","reason":"done"},"reason":"stop"}'
TERMINATE_FAILURE = '{"skill":"mission","action":"terminate","args":{"status":"failure","reason":"cannot"},"reason":"stop"}'
TERMINATE_CANCEL = '{"skill":"mission","action":"terminate","args":{"status":"cancelled","reason":"cancelled"},"reason":"stop"}'
QUERY = '{"skill":"system-state","action":"query_state","args":{},"reason":"look"}'
VERIFY = '{"skill":"perception","action":"verify_operator","args":{"operator_id":"operator_001"},"reason":"auth"}'


def _scenario(fault: FaultType | None = None, seed: int = 5) -> Scenario:
    faults = [FaultSpec(fault, default_params(fault, random.Random(seed)))] if fault else []
    return Scenario("wrench", "station_a", "operator_001", faults, require_confirmation=False)


def _run(texts: list[str], scenario: Scenario | None = None, seed: int = 91):
    env = MultiTurnDeliveryEnv(scenario or _scenario(), seed=seed)
    env.reset()
    for text in texts:
        _, done, _ = env.step(text)
        if done:
            break
    return env.record()


# ------------------------------------------------------------- terminal class


def test_success_is_labelled_success() -> None:
    record = run_episode(ExpertPolicy(), _scenario(), seed=92)
    assert terminal_class(record) == "success"


def test_claiming_delivery_that_never_happened_is_false_success() -> None:
    record = _run([VERIFY, TERMINATE_SUCCESS])
    assert record.outcome == "wrong_termination"
    assert terminal_class(record) == "false_success"


def test_giving_up_on_a_recoverable_mission_is_premature_stop() -> None:
    record = _run([VERIFY, TERMINATE_FAILURE])
    assert terminal_class(record) == "premature_stop"


def test_cancelling_without_a_cancel_request_is_wrong_cancel() -> None:
    record = _run([VERIFY, TERMINATE_CANCEL])
    assert terminal_class(record) == "wrong_cancel"


def test_looping_until_the_budget_runs_out_is_step_limit() -> None:
    record = _run([QUERY] * 40)
    assert record.outcome == "step_limit"
    assert terminal_class(record) == "step_limit"


def test_correct_abort_and_cancel_keep_their_own_labels() -> None:
    aborted = run_episode(ExpertPolicy(), _scenario(FaultType.TOOL_MISSING), seed=93)
    cancelled = run_episode(ExpertPolicy(), _scenario(FaultType.USER_CANCEL), seed=94)
    assert terminal_class(aborted) == "correct_abort"
    assert terminal_class(cancelled) == "cancelled_ok"


# ------------------------------------------------------------------- flags


def test_malformed_output_sets_both_malformed_and_invalid_flags() -> None:
    record = _run(["I will go get the wrench", VERIFY, TERMINATE_FAILURE])
    flags = behaviour_flags(record)
    assert "malformed_output" in flags and "invalid_recovery" in flags


def test_illegal_skill_sets_invalid_without_malformed() -> None:
    record = _run(['{"skill":"navigation","action":"teleport","args":{},"reason":"x"}', VERIFY, TERMINATE_FAILURE])
    flags = behaviour_flags(record)
    assert "invalid_recovery" in flags and "malformed_output" not in flags


def test_looping_sets_the_stalled_flag() -> None:
    record = _run([VERIFY] + [QUERY] * 10)
    assert "stalled" in behaviour_flags(record)


def test_the_expert_never_stalls_on_the_benchmark_definition() -> None:
    """The stall threshold is calibrated so a full-recovery policy scores zero."""
    for fault in FaultType:
        record = run_episode(ExpertPolicy(), _scenario(fault, seed=7), seed=95)
        assert record.summary["recovered"]
        assert episode_metrics(record).max_no_progress < NO_PROGRESS_THRESHOLD


# ---------------------------------------------------------------- aggregates


def test_repeated_calls_count_identical_calls_in_the_window() -> None:
    metrics = episode_metrics(_run([VERIFY, QUERY, QUERY, QUERY, TERMINATE_FAILURE]))
    assert metrics.repeated_calls == 2  # the second and third query


def test_reason_text_does_not_hide_a_repeat() -> None:
    reworded = QUERY.replace('"reason":"look"', '"reason":"checking once more"')
    metrics = episode_metrics(_run([VERIFY, QUERY, reworded, TERMINATE_FAILURE]))
    assert metrics.repeated_calls == 1


def test_recovery_success_ignores_clean_episodes() -> None:
    clean = run_episode(ExpertPolicy(), _scenario(), seed=96)
    faulted = _run([VERIFY, TERMINATE_FAILURE], _scenario(FaultType.NAV_TIMEOUT))
    summary = aggregate([episode_metrics(clean), episode_metrics(faulted)])
    assert summary.episodes == 2
    assert summary.faulted_episodes == 1
    assert summary.task_success == 0.5
    assert summary.recovery_success == 0.0  # the only faulted episode failed


def test_invalid_skill_rate_is_per_call_not_per_episode() -> None:
    record = _run(['{"skill":"navigation","action":"teleport","args":{},"reason":"x"}', VERIFY, TERMINATE_FAILURE])
    summary = aggregate([episode_metrics(record)])
    assert summary.total_calls == 3
    assert summary.invalid_skill_rate == round(1 / 3, 4)


def test_buckets_split_by_fault_family() -> None:
    records = [
        run_episode(ExpertPolicy(), _scenario(), seed=97),
        run_episode(ExpertPolicy(), _scenario(FaultType.NAV_TIMEOUT), seed=98),
        run_episode(ExpertPolicy(), _scenario(FaultType.GRASP_FAILURE), seed=99),
    ]
    buckets = bucketed([episode_metrics(record) for record in records], lambda item: item.fault_bucket)
    assert set(buckets) == {"clean", "navigation", "manipulation"}
    assert buckets["clean"].episodes == 1


def test_report_contains_every_section_and_renders_a_table() -> None:
    records = [run_episode(RuleBaselinePolicy(), _scenario(fault, seed=4), seed=100) for fault in FaultType]
    report = evaluate_records(records, "smoke")
    assert set(report) == {"label", "overall", "by_fault_bucket", "by_fault", "by_failing_phase", "by_length", "episodes"}
    assert len(report["episodes"]) == len(records)
    table = markdown_table(report)
    assert table.startswith("| bucket |")
    assert "overall" in table


def test_failing_phase_points_at_where_it_went_wrong() -> None:
    metrics = episode_metrics(_run([VERIFY, TERMINATE_FAILURE]))
    assert metrics.failing_phase != "done"
    success = episode_metrics(run_episode(ExpertPolicy(), _scenario(), seed=101))
    assert success.failing_phase == "done"


def test_length_buckets_are_contiguous() -> None:
    from training.evaluate import length_bucket

    assert length_bucket(1) == "short(<=8)"
    assert length_bucket(8) == "short(<=8)"
    assert length_bucket(9) == "medium(9-14)"
    assert length_bucket(14) == "medium(9-14)"
    assert length_bucket(15) == "long(>=15)"
