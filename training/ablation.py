"""Ablation table: compare conditions only at equal budgets.

A run is a JSON record with at least ``condition``, ``budgets`` and the metric
fields to display. Mixing budgets silently flatters whichever run was given
more turns, so the table refuses to render incomparable runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BUDGET_KEYS = ("max_turns", "max_tokens_chars", "max_wall_s")
METRIC_COLUMNS = (
    ("task_success", "success"),
    ("recovery_success", "recovery"),
    ("false_success_rate", "false_success"),
    ("premature_stop_rate", "premature_stop"),
    ("invalid_skill_rate", "invalid"),
    ("uncertain_outcome_rate", "uncertain"),
    ("duplicate_effect_rate", "duplicate"),
    ("actions", "actions"),
)


class BudgetMismatch(ValueError):
    """Raised when runs in one table were produced under different budgets."""


def _budget_tuple(record: dict) -> tuple:
    budgets = record.get("budgets") or {}
    return tuple(budgets.get(key) for key in BUDGET_KEYS)


def load_runs(paths: list[Path]) -> list[dict]:
    runs = []
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        record.setdefault("condition", path.stem)
        runs.append(record)
    return runs


def require_comparable(runs: list[dict]) -> tuple:
    if not runs:
        raise ValueError("no runs to compare")
    reference = _budget_tuple(runs[0])
    for record in runs[1:]:
        if _budget_tuple(record) != reference:
            raise BudgetMismatch(
                f"{record.get('condition')!r} used budgets {_budget_tuple(record)}, "
                f"{runs[0].get('condition')!r} used {reference}"
            )
    return reference


def _format(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.1%}"
    return str(value)


def render_table(runs: list[dict]) -> str:
    budgets = require_comparable(runs)
    header = "| condition | " + " | ".join(label for _, label in METRIC_COLUMNS) + " |"
    divider = "|" + "---|" * (len(METRIC_COLUMNS) + 1)
    lines = [
        f"Budgets: turns={budgets[0]}, chars={budgets[1]}, wall={budgets[2]}",
        "",
        header,
        divider,
    ]
    for record in runs:
        cells = [_format(record.get(key)) for key, _ in METRIC_COLUMNS]
        lines.append(f"| {record['condition']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the ablation table.")
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    table = render_table(load_runs(args.runs))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(table, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
