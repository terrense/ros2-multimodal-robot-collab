#!/usr/bin/env python3
"""Turn evaluation JSONs into the comparison tables the experiment is about.

    python -m training.report --eval-dir runs/eval --out docs/results.md
    python -m training.report --update docs/results.md
    python -m training.report --verify docs/results.md

Three tables:

    1. arms          measured metric per arm, next to the pre-registered target
    2. failure modes what each arm actually fixed, which is the real result --
                     "OPD reduced premature stops, PPO raised task success" is a
                     claim about this table, not about the headline number
    3. buckets       per-fault-family success, so an average cannot hide a family
                     that got worse

Arms that have not been run yet show as pending rather than being omitted, so the
report doubles as a checklist of what is still missing. Nothing here invents a
number: every cell comes from a file produced by `training.evaluate`.

`docs/results.md` mixes hand-written prose with generated tables. The generated
part lives between `<!-- BEGIN GENERATED: name -->` markers and is owned by this
module; everything outside the markers survives a regeneration, so a conclusion
written by a human is not eaten by the next run.

Two rules make the mechanism safe to run unattended:

    * a cell is only filled when a run record backs the evaluation file, and
    * a regeneration that would drop an already-published measurement is
      refused rather than silently applied (`EvidenceLost`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from .audit import audit
from .generated_block import begin_marker, end_marker, extract_block, replace_block
from .run_record import load_run_records

DEFAULT_MATRIX = Path("training/configs/experiment_matrix.yaml")
DEFAULT_CALIBRATION = Path("benchmarks/calibration_v1.json")
FAILURE_ROWS = ("premature_stop", "false_success", "wrong_cancel", "step_limit")
FLAG_ROWS = ("invalid_recovery", "malformed_output", "observation_misuse", "safety_violation", "stalled")

#: Keys that would turn an L1 result into a physical claim. The generator has
#: no column for L2, L3 or real-robot performance, so it refuses a matrix that
#: declares one (P7-R9).
FORBIDDEN_CLAIM_FIELDS = frozenset(
    {
        "physical_robot_success",
        "real_robot_success",
        "hardware_success",
        "l2_success",
        "l3_success",
        "sim_to_real",
        "physical_effect_verified",
    }
)

#: The blocks this generator owns. Order is the order they appear in the
#: document; the name is what the markers carry.
BLOCK_NAMES = ("summary", "arms", "failures", "buckets", "audit", "ablations")

#: Cells that mean "there is no number here".
_EMPTY_CELLS = {"", "--", "pending", "unverified", "not run", "no run record"}


class EvidenceLost(RuntimeError):
    """Raised when a regeneration would drop a measurement that is already published."""


class MissingBlocks(RuntimeError):
    """Raised when the target document carries none of the generated blocks."""


def load_matrix(path: Path) -> dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    claims = _claim_fields(data)
    if claims:
        raise ValueError(
            f"{path} declares physical-claim fields the generator has no column for: "
            + ", ".join(sorted(claims))
            + ". Results measured on L1 cannot be presented as physical-robot results."
        )
    return data


def _claim_fields(node: Any) -> set[str]:
    """Every forbidden key anywhere in a nested structure."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in FORBIDDEN_CLAIM_FIELDS:
                found.add(str(key))
            found |= _claim_fields(value)
    elif isinstance(node, list):
        for item in node:
            found |= _claim_fields(item)
    return found


def load_evaluations(directory: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    if not directory.exists():
        return reports
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "overall" in data:
            reports[str(data.get("label") or path.stem)] = data
    return reports


def load_all_evaluations(directories: Iterable[Path]) -> dict[str, dict[str, Any]]:
    """Load every evaluation file from several directories.

    Later directories win, so a fresh local run in `runs/eval` overrides the
    committed copy in `results/eval` without anyone having to delete anything.
    """
    reports: dict[str, dict[str, Any]] = {}
    for directory in directories:
        reports.update(load_evaluations(directory))
    return reports


def load_all_run_records(directories: Iterable[Path]) -> set[str]:
    """Names of every arm that has a run record, across several directories."""
    names: set[str] = set()
    for directory in directories:
        names |= set(load_run_records(directory))
    return names


def _percent(value: Any) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "--"


def _verified_names(matrix: dict[str, Any], reports: dict[str, dict[str, Any]], run_names: set[str] | None) -> list[str]:
    """Arm names whose numbers may be shown.

    An arm with an evaluation file but no matching run record is not evidence,
    so it is dropped here as well as in the arms table.
    """
    names = []
    for arm in matrix["arms"]:
        name = arm["name"]
        if name not in reports:
            continue
        if run_names is not None and name not in run_names:
            continue
        names.append(name)
    return names


def arms_table(
    matrix: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    run_names: set[str] | None = None,
) -> str:
    metric = matrix.get("metric", "recovery_success")
    header = (
        f"| arm | target ({metric}) | measured | delta vs baseline | task success | "
        "invalid | safety | actions | status |"
    )
    lines = [header, "|" + "|".join(["---"] * 9) + "|"]
    baseline = None
    baseline_name = "rule_baseline"
    for arm in matrix["arms"]:
        name = arm["name"]
        if name != baseline_name or name not in reports:
            continue
        if run_names is None or name in run_names:
            baseline = float(reports[name]["overall"].get(metric, 0.0))
    for arm in matrix["arms"]:
        name = arm["name"]
        report = reports.get(name)
        target = float(arm["target"])
        if report is None:
            lines.append(
                f"| {name} | {target:.1%} | pending | -- | -- | -- | -- | -- | not run |"
            )
            continue
        if run_names is not None and name not in run_names:
            # A metrics file without a matching run record is not evidence.
            lines.append(
                f"| {name} | {target:.1%} | unverified | unverified | unverified | unverified | "
                "unverified | unverified | no run record |"
            )
            continue
        overall = report["overall"]
        measured = float(overall.get(metric, 0.0))
        delta = "--" if baseline is None or name == baseline_name else f"{measured - baseline:+.1%}"
        status = "met" if measured >= target else f"missed ({measured - target:+.1%})"
        lines.append(
            f"| {name} | {target:.1%} | {measured:.1%} | {delta} | {_percent(overall['task_success'])} | "
            f"{_percent(overall['invalid_skill_rate'])} | {_percent(overall['safety_violation_rate'])} | "
            f"{overall['mean_actions']:.1f} | {status} |"
        )
    return "\n".join(lines)


def failure_table(
    matrix: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    run_names: set[str] | None = None,
) -> str:
    names = _verified_names(matrix, reports, run_names)
    if not names:
        return "_No evaluations available yet._"
    lines = ["| failure mode | " + " | ".join(names) + " |", "|" + "|".join(["---"] * (len(names) + 1)) + "|"]
    for row in FAILURE_ROWS:
        cells = []
        for name in names:
            overall = reports[name]["overall"]
            count = overall["terminal_classes"].get(row, 0)
            cells.append(f"{count / max(overall['episodes'], 1):.1%}")
        lines.append("| " + row + " | " + " | ".join(cells) + " |")
    for row in FLAG_ROWS:
        cells = []
        for name in names:
            overall = reports[name]["overall"]
            count = overall["flags"].get(row, 0)
            cells.append(f"{count / max(overall['episodes'], 1):.1%}")
        lines.append("| flag: " + row + " | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def bucket_table(
    matrix: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    run_names: set[str] | None = None,
    metric: str = "task_success",
) -> str:
    names = _verified_names(matrix, reports, run_names)
    if not names:
        return "_No evaluations available yet._"
    buckets = sorted({bucket for name in names for bucket in reports[name]["by_fault_bucket"]})
    lines = ["| fault family | " + " | ".join(names) + " |", "|" + "|".join(["---"] * (len(names) + 1)) + "|"]
    for bucket in buckets:
        cells = []
        for name in names:
            data = reports[name]["by_fault_bucket"].get(bucket)
            cells.append(_percent(data[metric]) if data else "--")
        lines.append("| " + bucket + " | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def regression_table(reports: dict[str, dict[str, Any]], current: str, previous: str) -> str:
    """Checkpoint regression: what changed between two runs, metric by metric."""
    if current not in reports or previous not in reports:
        return f"_Missing evaluation for {current!r} or {previous!r}._"
    keys = (
        "task_success",
        "recovery_success",
        "invalid_skill_rate",
        "safety_violation_rate",
        "repeated_call_rate",
        "no_progress_ratio",
    )
    lines = [f"| metric | {previous} | {current} | delta |", "|---|---|---|---|"]
    for key in keys:
        before = float(reports[previous]["overall"].get(key, 0.0))
        after = float(reports[current]["overall"].get(key, 0.0))
        lines.append(f"| {key} | {before:.1%} | {after:.1%} | {after - before:+.1%} |")
    lines.append(
        f"| mean_actions | {reports[previous]['overall']['mean_actions']:.1f} | "
        f"{reports[current]['overall']['mean_actions']:.1f} | "
        f"{reports[current]['overall']['mean_actions'] - reports[previous]['overall']['mean_actions']:+.1f} |"
    )
    return "\n".join(lines)


def audit_table(
    matrix: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    run_names: set[str] | None = None,
) -> str:
    names = _verified_names(matrix, reports, run_names)
    if not names:
        return "_No evaluations available yet._"
    lines = ["| arm | proxy_gap | flags |", "|---|---|---|"]
    for name in names:
        report = audit(reports[name]["overall"])
        gap = "--" if report["proxy_gap"] is None else f"{report['proxy_gap']:+.2f}"
        flags = ", ".join(report["flags"]) or "-"
        lines.append(f"| {name} | {gap} | {flags} |")
    return "\n".join(lines)


def summary_line(matrix: dict[str, Any], reports: dict[str, dict[str, Any]], run_names: set[str] | None = None) -> str:
    done = _verified_names(matrix, reports, run_names)
    pending = [arm["name"] for arm in matrix["arms"] if arm["name"] not in done]
    line = f"Runs completed: {len(done)}/{len(matrix['arms'])}."
    if pending:
        line += " Pending: " + ", ".join(pending) + "."
    return line


def calibration_status(path: Path | None = None) -> dict[str, Any]:
    """What the live stack has confirmed about L1's fault model.

    L1's fault parameters are configured, not measured. The calibration
    manifest is where the live stack's answer is recorded, and its absence is
    itself an answer: nothing has been confirmed yet.
    """
    target = path or DEFAULT_CALIBRATION
    if not target.exists():
        return {"present": False, "calibrated": [], "uncalibrated": [], "total": 0, "all_calibrated": False}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"present": False, "calibrated": [], "uncalibrated": [], "total": 0, "all_calibrated": False}
    families = data.get("families") or []
    return {
        "present": True,
        "calibrated": [str(family["name"]) for family in families if family.get("calibrated")],
        "uncalibrated": [
            {"name": str(family["name"]), "reason": str(family.get("reason", ""))}
            for family in families
            if not family.get("calibrated")
        ],
        "total": len(families),
        "all_calibrated": bool(data.get("all_calibrated")),
    }


def calibration_line(status: dict[str, Any]) -> str:
    """The header line that says how far these results are from physical."""
    if not status["present"]:
        return (
            "L2 status: no calibration manifest -- the live stack has confirmed none of L1's "
            "fault behaviour. Every result below is L1 only."
        )
    if status["all_calibrated"] and status["total"]:
        return (
            f"L2 status: all {status['total']} fault families calibrated against the live stack."
        )
    pending = ", ".join(f"{item['name']} ({item['reason']})" for item in status["uncalibrated"])
    return (
        f"L2 status: {len(status['calibrated'])}/{status['total']} fault families calibrated "
        f"against the live stack; not calibrated: {pending}. Every result below is L1 only."
    )


def ablation_block(matrix: dict[str, Any]) -> str:
    lines = []
    for ablation in matrix.get("ablations", []):
        lines.append(f"- **{ablation['name']}** -- {ablation['question'].strip()}")
        lines.append(f"  `{ablation['command']}`")
    return "\n".join(lines) if lines else "_No ablations defined._"


def render_blocks(
    matrix: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    run_names: set[str] | None = None,
    calibration: dict[str, Any] | None = None,
) -> dict[str, str]:
    """The generated content of the document, one entry per block."""
    status = calibration if calibration is not None else calibration_status()
    return {
        "summary": summary_line(matrix, reports, run_names) + "\n" + calibration_line(status),
        "arms": arms_table(matrix, reports, run_names),
        "failures": failure_table(matrix, reports, run_names),
        "buckets": bucket_table(matrix, reports, run_names),
        "audit": audit_table(matrix, reports, run_names),
        "ablations": ablation_block(matrix),
    }


def _marker_block(name: str, body: str) -> str:
    return f"{begin_marker(name)}\n{body.rstrip()}\n{end_marker(name)}"


def render(
    matrix: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    run_names: set[str] | None = None,
    calibration: dict[str, Any] | None = None,
) -> str:
    blocks = render_blocks(matrix, reports, run_names, calibration)
    sections = [
        "# Results",
        "",
        f"Benchmark: `{matrix.get('benchmark')}` (frozen, held out).",
        f"Primary metric: `{matrix.get('metric')}` -- share of fault-injected episodes that end correctly.",
        "",
        _marker_block("summary", blocks["summary"]),
        "",
        "Targets are pre-registered acceptance criteria, not results. Measured columns",
        "are produced by `python -m training.evaluate`; an empty cell means the run has",
        "not happened.",
        "",
        "## Arms",
        "",
        _marker_block("arms", blocks["arms"]),
        "",
        "## Failure decomposition",
        "",
        "The headline number says how often the agent succeeded. This table says what",
        "went wrong when it did not, which is what the supervision-source comparison is",
        "actually about.",
        "",
        _marker_block("failures", blocks["failures"]),
        "",
        "## Per-fault-family task success",
        "",
        _marker_block("buckets", blocks["buckets"]),
        "",
        "## Reward-hacking audit",
        "",
        _marker_block("audit", blocks["audit"]),
        "",
        "## Ablations",
        "",
        _marker_block("ablations", blocks["ablations"]),
        "",
    ]
    return "\n".join(sections)


# ------------------------------------------------------------------- guarding


def _is_measurement(cell: str) -> bool:
    text = cell.strip()
    if text.lower() in _EMPTY_CELLS:
        return False
    try:
        float(text.rstrip("%"))
    except ValueError:
        return False
    return True


def _arm_rows(table: str) -> dict[str, str]:
    """Arm name -> measured cell, parsed out of a rendered arms table."""
    rows: dict[str, str] = {}
    for line in table.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if set(cells[0]) <= set("-: "):
            continue
        if cells[0].lower() == "arm":
            continue
        rows[cells[0]] = cells[2]
    return rows


def lost_measurements(old_block: str | None, new_block: str) -> list[str]:
    """Arms that carried a number and would lose it in the new rendering."""
    if not old_block:
        return []
    old_rows = _arm_rows(old_block)
    new_rows = _arm_rows(new_block)
    lost = []
    for arm, value in old_rows.items():
        if not _is_measurement(value):
            continue
        new_value = new_rows.get(arm, "")
        if not _is_measurement(new_value):
            lost.append(f"{arm}: {value} -> {new_value or 'missing'}")
    return lost


def update_document(
    document: str,
    blocks: dict[str, str],
    *,
    force: bool = False,
) -> str:
    """Replace every generated block, refusing to lose a published measurement."""
    if not any(extract_block(document, name) is not None for name in BLOCK_NAMES):
        raise MissingBlocks(
            "the document carries none of the generated blocks; "
            "write a fresh one with --out instead of updating it"
        )
    updated = document
    for name in BLOCK_NAMES:
        if name not in blocks:
            continue
        old = extract_block(updated, name)
        if name == "arms":
            lost = lost_measurements(old, blocks[name])
            if lost and not force:
                raise EvidenceLost(
                    "refusing to drop a published measurement: "
                    + "; ".join(lost)
                    + ". The evaluation artifact is missing -- re-run the arm, or pass --force."
                )
        updated = replace_block(updated, name, blocks[name])
    return updated


def stale_blocks(document: str, blocks: dict[str, str]) -> list[str]:
    """Blocks whose content no longer matches what the generator would write."""
    stale = []
    for name in BLOCK_NAMES:
        if name not in blocks:
            continue
        current = extract_block(document, name)
        if current is None:
            stale.append(name)
            continue
        if current.strip() != blocks[name].strip():
            stale.append(name)
    return stale


# ------------------------------------------------------------------------ cli


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--eval-dir",
        type=Path,
        nargs="+",
        default=[Path("results/eval"), Path("runs/eval")],
        help="directories holding evaluation JSONs; later directories win",
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument(
        "--calibration",
        type=Path,
        default=DEFAULT_CALIBRATION,
        help="the L2 calibration manifest the header reports from",
    )
    parser.add_argument("--out", type=Path, default=None, help="write a whole document")
    parser.add_argument("--update", type=Path, default=None, help="refresh the blocks inside an existing document")
    parser.add_argument("--verify", type=Path, default=None, help="exit non-zero if the blocks are stale")
    parser.add_argument(
        "--run-dir",
        type=Path,
        nargs="+",
        default=[Path("results/run_records"), Path("runs/run_records")],
        help="directories holding run records; later directories win",
    )
    parser.add_argument(
        "--require-run-records",
        dest="require_run_records",
        action="store_true",
        default=True,
        help="render unverified for arms without a matching run record (default)",
    )
    parser.add_argument(
        "--no-require-run-records",
        dest="require_run_records",
        action="store_false",
        help="render measured values even when no run record backs them",
    )
    parser.add_argument("--force", action="store_true", help="allow a regeneration that drops a measurement")
    parser.add_argument("--regression", nargs=2, metavar=("PREVIOUS", "CURRENT"), default=None)
    args = parser.parse_args()

    matrix = load_matrix(args.matrix)
    reports = load_all_evaluations(args.eval_dir)

    if args.regression:
        previous, current = args.regression
        print(regression_table(reports, current, previous))
        return

    run_names = load_all_run_records(args.run_dir) if args.require_run_records else None
    blocks = render_blocks(matrix, reports, run_names, calibration_status(args.calibration))

    if args.verify:
        document = args.verify.read_text(encoding="utf-8")
        stale = stale_blocks(document, blocks)
        if stale:
            print(f"stale blocks: {', '.join(stale)}")
            sys.exit(1)
        print(f"blocks up to date: {args.verify}")
        return

    if args.update:
        document = args.update.read_text(encoding="utf-8")
        try:
            updated = update_document(document, blocks, force=args.force)
        except (EvidenceLost, MissingBlocks) as error:
            print(f"refused to update {args.update}: {error}", file=sys.stderr)
            sys.exit(1)
        args.update.parent.mkdir(parents=True, exist_ok=True)
        args.update.write_text(updated, encoding="utf-8", newline="\n")
        print(f"-> {args.update}")
        return

    document = render(matrix, reports, run_names)
    print(document)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(document, encoding="utf-8", newline="\n")
        print(f"-> {args.out}")


if __name__ == "__main__":
    main()
