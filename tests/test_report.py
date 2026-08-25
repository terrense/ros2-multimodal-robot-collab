"""The report must show what has not been run, and must never invent a cell."""

from __future__ import annotations

import json
from pathlib import Path

from training.report import DEFAULT_MATRIX, arms_table, bucket_table, failure_table, load_evaluations, load_matrix, regression_table, render


def _report(label: str, **overrides) -> dict:
    overall = {
        "episodes": 100,
        "task_success": 0.5,
        "recovery_success": 0.6,
        "invalid_skill_rate": 0.02,
        "safety_violation_rate": 0.01,
        "mean_actions": 10.0,
        "repeated_call_rate": 0.1,
        "no_progress_ratio": 0.05,
        "terminal_classes": {"success": 50, "premature_stop": 30},
        "flags": {"stalled": 10},
    }
    overall.update(overrides)
    return {
        "label": label,
        "overall": overall,
        "by_fault_bucket": {"clean": dict(overall), "navigation": dict(overall)},
    }


def test_matrix_ships_with_the_repository() -> None:
    matrix = load_matrix(DEFAULT_MATRIX)
    assert matrix["arms"] and matrix["metric"] == "recovery_success"


def test_pending_arms_are_listed_not_hidden() -> None:
    matrix = load_matrix(DEFAULT_MATRIX)
    table = arms_table(matrix, {"rule_baseline": _report("rule_baseline")})
    assert "not run" in table
    assert table.count("pending") == len(matrix["arms"]) - 1
    assert "rule_baseline" in table


def test_measured_value_is_compared_against_the_target() -> None:
    matrix = load_matrix(DEFAULT_MATRIX)
    met = arms_table(matrix, {"rule_baseline": _report("rule_baseline", recovery_success=0.90)})
    missed = arms_table(matrix, {"rule_baseline": _report("rule_baseline", recovery_success=0.10)})
    assert "| met |" in met
    assert "missed" in missed


def test_failure_table_shows_one_column_per_completed_arm() -> None:
    matrix = load_matrix(DEFAULT_MATRIX)
    table = failure_table(matrix, {"rule_baseline": _report("rule_baseline"), "ppo_progress": _report("ppo_progress")})
    header = table.splitlines()[0]
    assert "rule_baseline" in header and "ppo_progress" in header
    assert "premature_stop" in table and "flag: stalled" in table


def test_tables_say_so_when_nothing_has_been_run() -> None:
    matrix = load_matrix(DEFAULT_MATRIX)
    assert "No evaluations available" in failure_table(matrix, {})
    assert "No evaluations available" in bucket_table(matrix, {})


def test_render_reports_progress_and_keeps_targets_labelled_as_targets() -> None:
    matrix = load_matrix(DEFAULT_MATRIX)
    document = render(matrix, {"rule_baseline": _report("rule_baseline")})
    assert f"Runs completed: 1/{len(matrix['arms'])}" in document
    assert "pre-registered acceptance criteria, not results" in document
    assert "## Failure decomposition" in document
    assert "## Ablations" in document


def test_regression_table_reports_signed_deltas() -> None:
    reports = {
        "before": _report("before", task_success=0.50),
        "after": _report("after", task_success=0.65),
    }
    table = regression_table(reports, "after", "before")
    assert "+15.0%" in table
    assert "| task_success |" in table


def test_regression_table_is_explicit_about_missing_runs() -> None:
    assert "Missing evaluation" in regression_table({}, "after", "before")


def test_evaluations_are_loaded_by_label_not_filename(tmp_path: Path) -> None:
    path = tmp_path / "some_file.json"
    path.write_text(json.dumps(_report("ppo_progress")), encoding="utf-8")
    (tmp_path / "not_a_report.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    loaded = load_evaluations(tmp_path)
    assert set(loaded) == {"ppo_progress"}


def test_missing_eval_dir_is_not_an_error(tmp_path: Path) -> None:
    assert load_evaluations(tmp_path / "nope") == {}
