#!/usr/bin/env python3
"""Inspect any dataset in this repo and check it against the held-out benchmark.

    python -m training.data.dataset_stats data/sft/train.jsonl --benchmark benchmarks/fault_injection_v1.jsonl

Handles the three line formats in use: SFT chat samples (`messages`), DPO pairs
(`prompt`/`chosen`/`rejected`) and rollout trajectories (`record`).

The leakage check compares scenario fingerprints, which every scenario id ends
with, so it works on a dataset file alone without re-deriving the scenarios.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .scenario_bank import load_specs


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def detect_format(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "empty"
    first = rows[0]
    if "messages" in first:
        return "sft"
    if "chosen" in first and "rejected" in first:
        return "dpo"
    if "record" in first:
        return "trajectory"
    return "unknown"


def _histogram(values: Iterable[int]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {}
    def percentile(fraction: float) -> float:
        return float(ordered[min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))])
    return {
        "min": float(ordered[0]),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "max": float(ordered[-1]),
        "mean": round(sum(ordered) / len(ordered), 2),
    }


def sft_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    turns = [sum(1 for message in row["messages"] if message["role"] == "assistant") for row in rows]
    chars = [sum(len(message["content"]) for message in row["messages"]) for row in rows]
    metas = [row.get("meta") or {} for row in rows]
    return {
        "format": "sft",
        "samples": len(rows),
        "assistant_turns": _histogram(turns),
        "chars": _histogram(chars),
        "approx_tokens_p95": round(_histogram(chars).get("p95", 0) / 3.5, 1),
        "buckets": dict(sorted(Counter(meta.get("bucket") for meta in metas).items())),
        "outcomes": dict(sorted(Counter(meta.get("outcome") for meta in metas).items())),
    }


def dpo_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    metas = [row.get("meta") or {} for row in rows]
    gaps = [float(meta.get("score_gap", 0.0)) for meta in metas]
    return {
        "format": "dpo",
        "pairs": len(rows),
        "rules": dict(sorted(Counter(meta.get("rule") for meta in metas).items())),
        "failure_modes": dict(sorted(Counter(meta.get("failure_mode") for meta in metas).items())),
        "buckets": dict(sorted(Counter(meta.get("bucket") for meta in metas).items())),
        "recovery_state_share": round(sum(1 for meta in metas if meta.get("recovery_state")) / max(len(metas), 1), 3),
        "score_gap": {
            "mean": round(sum(gaps) / max(len(gaps), 1), 4),
            "min": round(min(gaps, default=0.0), 4),
            "max": round(max(gaps, default=0.0), 4),
        },
        "chosen_turns": _histogram([sum(1 for m in row["chosen"] if m["role"] == "assistant") for row in rows]),
    }


def trajectory_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    episodes = [row for row in rows if row.get("record") == "episode"]
    steps = [row for row in rows if row.get("record") == "step"]
    summaries = [episode.get("summary") or {} for episode in episodes]
    return {
        "format": "trajectory",
        "episodes": len(episodes),
        "steps": len(steps),
        "outcomes": dict(sorted(Counter(summary.get("outcome") for summary in summaries).items())),
        "recovered": round(sum(1 for summary in summaries if summary.get("recovered")) / max(len(summaries), 1), 4),
        "invalid_actions": sum(int(summary.get("invalid_actions", 0)) for summary in summaries),
        "safety_violations": sum(int(summary.get("safety_violations", 0)) for summary in summaries),
        "steps_per_episode": _histogram([int(summary.get("steps", 0)) for summary in summaries]),
    }


def _fingerprints(rows: list[dict[str, Any]]) -> set[str]:
    """Scenario fingerprints, recovered from the trailing field of each scenario id."""
    prints: set[str] = set()
    for row in rows:
        meta = row.get("meta") or {}
        episode_id = meta.get("episode_id") or row.get("episode_id") or ""
        parts = str(episode_id).rsplit("-", 1)
        if len(parts) == 2 and len(parts[1]) == 16:
            prints.add(parts[1])
    return prints


def leakage_report(rows: list[dict[str, Any]], benchmark_path: Path) -> dict[str, Any]:
    from .scenario_bank import fingerprint

    dataset_prints = _fingerprints(rows)
    benchmark_prints = {fingerprint(spec.scenario) for spec in load_specs(benchmark_path)}
    shared = sorted(dataset_prints & benchmark_prints)
    return {
        "dataset_scenarios": len(dataset_prints),
        "benchmark_scenarios": len(benchmark_prints),
        "shared_scenarios": len(shared),
        "examples": shared[:5],
        "clean": not shared,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--benchmark", type=Path, default=None, help="Cross-check for benchmark leakage.")
    args = parser.parse_args()

    rows = read_jsonl(args.path)
    kind = detect_format(rows)
    stats = {
        "sft": sft_stats,
        "dpo": dpo_stats,
        "trajectory": trajectory_stats,
    }.get(kind, lambda _: {"format": kind, "rows": len(rows)})(rows)
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    if args.benchmark:
        report = leakage_report(rows, args.benchmark)
        print("\nleakage check:")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report["clean"]:
            raise SystemExit("benchmark leakage detected")


if __name__ == "__main__":
    main()
