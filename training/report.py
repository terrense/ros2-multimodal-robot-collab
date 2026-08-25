#!/usr/bin/env python3
"""Turn evaluation JSONs into the comparison tables the experiment is about.

    python -m training.report --eval-dir runs/eval --out docs/results.md

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
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_MATRIX = Path("training/configs/experiment_matrix.yaml")
FAILURE_ROWS = ("premature_stop", "false_success", "wrong_cancel", "step_limit")
FLAG_ROWS = ("invalid_recovery", "malformed_output", "observation_misuse", "safety_violation", "stalled")


def load_matrix(path: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


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


def _percent(value: Any) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "--"


def arms_table(matrix: dict[str, Any], reports: dict[str, dict[str, Any]]) -> str:
    metric = matrix.get("metric", "recovery_success")
    header = f"| arm | target ({metric}) | measured | task success | invalid | safety | actions | status |"
    lines = [header, "|" + "|".join(["---"] * 8) + "|"]
    baseline = None
    for arm in matrix["arms"]:
        report = reports.get(arm["name"])
        target = float(arm["target"])
        if report is None:
            lines.append(f"| {arm['name']} | {target:.1%} | pending | -- | -- | -- | -- | not run |")
            continue
        overall = report["overall"]
        measured = float(overall.get(metric, 0.0))
        if baseline is None:
            baseline = measured
        status = "met" if measured >= target else f"missed ({measured - target:+.1%})"
        lines.append(
            f"| {arm['name']} | {target:.1%} | {measured:.1%} | {_percent(overall['task_success'])} | "
            f"{_percent(overall['invalid_skill_rate'])} | {_percent(overall['safety_violation_rate'])} | "
            f"{overall['mean_actions']:.1f} | {status} |"
        )
    return "\n".join(lines)


def failure_table(matrix: dict[str, Any], reports: dict[str, dict[str, Any]]) -> str:
    names = [arm["name"] for arm in matrix["arms"] if arm["name"] in reports]
    if not names:
        return "_No evaluations available yet._"
    lines = ["| failure mode | " + " | ".join(names) + " |", "|" + "|".join(["---"] * (len(names) + 1)) + "|"]
    for row in FAILURE_ROWS:
        cells = []
        for name in names:
            overall = reports[name]["overall"]
            count = overall["terminal_classes"].get(row, 0)
            cells.append(f"{count / max(overall['episodes'], 1):.1%}")
        lines.append(f"| {row} | " + " | ".join(cells) + " |")
    for row in FLAG_ROWS:
        cells = []
        for name in names:
            overall = reports[name]["overall"]
            count = overall["flags"].get(row, 0)
            cells.append(f"{count / max(overall['episodes'], 1):.1%}")
        lines.append(f"| flag: {row} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def bucket_table(matrix: dict[str, Any], reports: dict[str, dict[str, Any]], metric: str = "task_success") -> str:
    names = [arm["name"] for arm in matrix["arms"] if arm["name"] in reports]
    if not names:
        return "_No evaluations available yet._"
    buckets = sorted({bucket for name in names for bucket in reports[name]["by_fault_bucket"]})
    lines = ["| fault family | " + " | ".join(names) + " |", "|" + "|".join(["---"] * (len(names) + 1)) + "|"]
    for bucket in buckets:
        cells = []
        for name in names:
            data = reports[name]["by_fault_bucket"].get(bucket)
            cells.append(_percent(data[metric]) if data else "--")
        lines.append(f"| {bucket} | " + " | ".join(cells) + " |")
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


def render(matrix: dict[str, Any], reports: dict[str, dict[str, Any]]) -> str:
    completed = [arm["name"] for arm in matrix["arms"] if arm["name"] in reports]
    pending = [arm["name"] for arm in matrix["arms"] if arm["name"] not in reports]
    sections = [
        "# Results",
        "",
        f"Benchmark: `{matrix.get('benchmark')}` (frozen, held out).",
        f"Primary metric: `{matrix.get('metric')}` -- share of fault-injected episodes that end correctly.",
        "",
        f"Runs completed: {len(completed)}/{len(matrix['arms'])}."
        + (f" Pending: {', '.join(pending)}." if pending else ""),
        "",
        "Targets are pre-registered acceptance criteria, not results. Measured columns",
        "are produced by `python -m training.evaluate`; an empty cell means the run has",
        "not happened.",
        "",
        "## Arms",
        "",
        arms_table(matrix, reports),
        "",
        "## Failure decomposition",
        "",
        "The headline number says how often the agent succeeded. This table says what",
        "went wrong when it did not, which is what the supervision-source comparison is",
        "actually about.",
        "",
        failure_table(matrix, reports),
        "",
        "## Per-fault-family task success",
        "",
        bucket_table(matrix, reports),
        "",
        "## Ablations",
        "",
    ]
    for ablation in matrix.get("ablations", []):
        sections.append(f"- **{ablation['name']}** -- {ablation['question'].strip()}")
        sections.append(f"  `{ablation['command']}`")
    sections.append("")
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-dir", type=Path, default=Path("runs/eval"))
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--regression", nargs=2, metavar=("PREVIOUS", "CURRENT"), default=None)
    args = parser.parse_args()

    matrix = load_matrix(args.matrix)
    reports = load_evaluations(args.eval_dir)

    if args.regression:
        previous, current = args.regression
        print(regression_table(reports, current, previous))
        return

    document = render(matrix, reports)
    print(document)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(document, encoding="utf-8", newline="\n")
        print(f"-> {args.out}")


if __name__ == "__main__":
    main()
