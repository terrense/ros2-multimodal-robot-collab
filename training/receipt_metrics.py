"""Receipt-level metrics.

``training/evaluate.py`` measures the policy from trajectories. These metrics
measure the execution layer from receipts and ledger events — the things a
trajectory cannot show: an outcome that was never established, a command that
took effect twice, and how long a physical action actually took.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def receipt_metrics(rows: Sequence[dict]) -> dict:
    """``rows``: receipt dictionaries, optionally with a ``capability`` key."""
    calls = len(rows)
    uncertain = sum(1 for row in rows if row.get("execution_status") == "unknown")
    latencies = [
        (float(row["finished_at"]) - float(row["started_at"])) * 1000.0
        for row in rows
        if row.get("started_at") is not None and row.get("finished_at") is not None
    ]
    by_capability: dict[str, Counter] = {}
    for row in rows:
        capability = row.get("capability")
        if capability:
            by_capability.setdefault(capability, Counter())[
                str(row.get("execution_status"))
            ] += 1
    return {
        "calls": calls,
        "uncertain_outcome_rate": round(uncertain / calls, 4) if calls else 0.0,
        "latency_p50_ms": _round(percentile(latencies, 0.50)),
        "latency_p95_ms": _round(percentile(latencies, 0.95)),
        "by_capability": {
            capability: dict(counter) for capability, counter in sorted(by_capability.items())
        },
    }


def duplicate_effect_rate(events: Sequence[dict]) -> float:
    """Commands dispatched more than once, over commands dispatched at least once.

    ``events`` are ledger event rows: ``{"command_id": ..., "kind": ...}``.
    A second ``started`` for one command id means the same command reached the
    backend twice, which no retry policy may produce.
    """
    started: Counter = Counter(
        row["command_id"] for row in events if row.get("kind") == "started"
    )
    dispatched = len(started)
    if dispatched == 0:
        return 0.0
    duplicated = sum(1 for count in started.values() if count > 1)
    return round(duplicated / dispatched, 4)


def combine(*parts: dict) -> dict:
    merged: dict = {}
    for part in parts:
        merged.update(part)
    return merged


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
