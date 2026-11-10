"""The generated part of `docs/results.md` is owned by the generator.

Two properties matter more than the tables themselves:

    * prose written by hand survives a regeneration, and
    * a regeneration can never quietly delete a measurement that is published.

The second one is the reason `update_document` refuses instead of overwriting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.generated_block import extract_block
from training.report import (
    BLOCK_NAMES,
    EvidenceLost,
    MissingBlocks,
    arms_table,
    calibration_line,
    calibration_status,
    load_all_evaluations,
    load_matrix,
    render,
    render_blocks,
    stale_blocks,
    update_document,
)
from training.run_record import build_provenance, load_run_records, write_run_record

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX = REPO_ROOT / "training" / "configs" / "experiment_matrix.yaml"
RESULTS_DOC = REPO_ROOT / "docs" / "results.md"


def _matrix() -> dict:
    return load_matrix(MATRIX)


def _report(label: str = "rule_baseline", **overrides) -> dict:
    overall = {
        "episodes": 100,
        "task_success": 0.63,
        "recovery_success": 0.761,
        "invalid_skill_rate": 0.0,
        "safety_violation_rate": 0.0,
        "mean_actions": 10.5,
        "repeated_call_rate": 0.1,
        "no_progress_ratio": 0.05,
        "terminal_classes": {"success": 50, "premature_stop": 30},
        "flags": {"stalled": 10},
    }
    overall.update(overrides)
    return {"label": label, "overall": overall, "by_fault_bucket": {"clean": dict(overall)}}


# ------------------------------------------------------------------ rendering


def test_render_carries_every_generated_block():
    document = render(_matrix(), {"rule_baseline": _report()})
    for name in BLOCK_NAMES:
        assert f"<!-- BEGIN GENERATED: {name} -->" in document
        assert f"<!-- END GENERATED: {name} -->" in document


def test_hand_written_prose_survives_a_regeneration():
    document = (
        "# Results\n\nA conclusion a human wrote.\n\n"
        "<!-- BEGIN GENERATED: arms -->\n| stale |\n<!-- END GENERATED: arms -->\n\n"
        "And another one.\n"
    )
    blocks = render_blocks(_matrix(), {"rule_baseline": _report()})
    updated = update_document(document, blocks)
    assert "A conclusion a human wrote." in updated
    assert "And another one." in updated
    assert "| stale |" not in updated
    assert "76.1%" in updated


def test_update_is_idempotent():
    matrix = _matrix()
    reports = {"rule_baseline": _report()}
    document = render(matrix, reports)
    blocks = render_blocks(matrix, reports)
    assert update_document(document, blocks) == update_document(update_document(document, blocks), blocks)


def test_stale_blocks_finds_a_table_that_drifted():
    matrix = _matrix()
    reports = {"rule_baseline": _report()}
    document = render(matrix, reports)
    blocks = render_blocks(matrix, reports)
    assert stale_blocks(document, blocks) == []
    drifted = document.replace("76.1%", "99.9%")
    assert "arms" in stale_blocks(drifted, blocks)


def test_document_without_blocks_is_not_updated():
    blocks = render_blocks(_matrix(), {})
    with pytest.raises(MissingBlocks):
        update_document("# Results\n\nNo markers here.\n", blocks)


# -------------------------------------------------------------- the guard rail


def test_a_measurement_is_never_dropped_silently():
    matrix = _matrix()
    measured = render(matrix, {"rule_baseline": _report()})
    # The evaluation artifact is gone; only the target list is left.
    empty = render_blocks(matrix, {}, {"rule_baseline"})
    with pytest.raises(EvidenceLost) as error:
        update_document(measured, empty)
    assert "rule_baseline" in str(error.value)
    assert "76.1%" in str(error.value)


def test_the_guard_can_be_overridden_explicitly():
    matrix = _matrix()
    measured = render(matrix, {"rule_baseline": _report()})
    empty = render_blocks(matrix, {}, {"rule_baseline"})
    updated = update_document(measured, empty, force=True)
    assert "pending" in extract_block(updated, "arms")


def test_a_different_measurement_is_not_a_loss():
    matrix = _matrix()
    measured = render(matrix, {"rule_baseline": _report()})
    remeasured = render_blocks(matrix, {"rule_baseline": _report(recovery_success=0.80)})
    updated = update_document(measured, remeasured)
    assert "80.0%" in updated


# ----------------------------------------------------------------- the tables


def test_arms_table_shows_the_delta_against_the_baseline():
    matrix = _matrix()
    reports = {"rule_baseline": _report(), "sft": _report("sft", recovery_success=0.80)}
    table = arms_table(matrix, reports, {"rule_baseline", "sft"})
    assert "+3.9%" in table
    assert "-- |" in table  # the baseline itself has no delta against itself


def test_evaluations_from_several_directories_are_merged():
    first = REPO_ROOT / "results" / "eval"
    second = REPO_ROOT / "results" / "eval"
    assert load_all_evaluations([first, second])


def test_later_directories_win():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a").mkdir()
        (root / "b").mkdir()
        (root / "a" / "x.json").write_text(json.dumps(_report("x", recovery_success=0.5)), encoding="utf-8")
        (root / "b" / "x.json").write_text(json.dumps(_report("x", recovery_success=0.9)), encoding="utf-8")
        merged = load_all_evaluations([root / "a", root / "b"])
        assert merged["x"]["overall"]["recovery_success"] == 0.9


# ------------------------------------------------------------- run records


def test_run_records_are_written_from_the_live_prompt_surface(tmp_path: Path):
    benchmark = REPO_ROOT / "benchmarks" / "fault_injection_v1.jsonl"
    out = tmp_path / "rule_baseline.json"
    write_run_record(
        name="rule_baseline",
        kind="eval",
        model_id="rule_baseline",
        benchmark=benchmark,
        policy="rules",
        episodes=400,
        artifacts=["results/eval/rule_baseline.json"],
        out=out,
    )
    record = load_run_records(tmp_path)["rule_baseline"]
    assert record.provenance == build_provenance("rule_baseline", benchmark).to_dict()
    assert record.policy == "rules"
    assert record.budgets["episodes"] == 400
    assert record.budgets["max_turns"] == 30


# ------------------------------------------------- the committed document


def test_the_header_says_how_far_the_results_are_from_physical(tmp_path: Path) -> None:
    """No calibration manifest means nothing has been confirmed on the live stack."""
    line = calibration_line(calibration_status(tmp_path / "missing.json"))
    assert "no calibration manifest" in line
    assert "L1 only" in line


def test_the_header_calls_out_the_families_that_are_not_calibrated(tmp_path: Path) -> None:
    path = tmp_path / "calibration_v1.json"
    path.write_text(
        json.dumps(
            {
                "version": "calibration_v1",
                "all_calibrated": False,
                "families": [
                    {"name": "nav_timeout", "calibrated": False, "reason": "not_measured"},
                    {"name": "tool_missing", "calibrated": True, "reason": "ok"},
                ],
            }
        ),
        encoding="utf-8",
    )
    line = calibration_line(calibration_status(path))
    assert "1/2" in line
    assert "nav_timeout (not_measured)" in line
    assert "L1 only" in line


def test_the_committed_results_document_is_up_to_date():
    """`docs/results.md` must be exactly what the generator writes.

    This is the test that keeps the hand-written prose and the generated tables
    from drifting apart: change a table without regenerating the document and
    this fails.
    """
    matrix = load_matrix(MATRIX)
    reports = load_all_evaluations([REPO_ROOT / "results" / "eval", REPO_ROOT / "runs" / "eval"])
    run_names = set(load_run_records(REPO_ROOT / "results" / "run_records"))
    blocks = render_blocks(matrix, reports, run_names)
    document = RESULTS_DOC.read_text(encoding="utf-8")
    assert stale_blocks(document, blocks) == []


def test_the_committed_baseline_is_the_measured_number():
    """The published baseline must come from a committed artifact, not a memory."""
    artifact = REPO_ROOT / "results" / "eval" / "rule_baseline.json"
    data = json.loads(artifact.read_text(encoding="utf-8"))
    assert data["overall"]["recovery_success"] == 0.7611
    assert data["overall"]["task_success"] == 0.63
    records = load_run_records(REPO_ROOT / "results" / "run_records")
    assert "rule_baseline" in records
