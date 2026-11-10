"""Run records: the evidence a reported number requires.

A metrics file alone is not enough. Every reported cell must be backed by a run
record that names the model, prompt, schema, benchmark, seeds and budgets that
produced it. The report renders `unverified` for anything else.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .provenance import Provenance, require_trainable


class RunRecordMismatch(ValueError):
    """Raised when a run record does not match the provenance the report expects."""


@dataclass
class RunRecord:
    kind: str
    name: str
    provenance: dict[str, Any]
    seeds: list[int] = field(default_factory=list)
    budgets: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "provenance": self.provenance,
            "seeds": list(self.seeds),
            "budgets": dict(self.budgets),
            "artifacts": list(self.artifacts),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunRecord":
        return cls(
            kind=str(data["kind"]),
            name=str(data["name"]),
            provenance=dict(data["provenance"]),
            seeds=list(data.get("seeds", [])),
            budgets=dict(data.get("budgets", {})),
            artifacts=list(data.get("artifacts", [])),
            created_at=float(data.get("created_at", 0.0)),
        )

    def write(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, path: Path | str) -> "RunRecord":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def load_run_records(directory: Path | str) -> dict[str, RunRecord]:
    records: dict[str, RunRecord] = {}
    root = Path(directory)
    if not root.exists():
        return records
    for path in sorted(root.rglob("*.json")):
        try:
            record = RunRecord.load(path)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        records[record.name] = record
    return records


def matches(record: RunRecord, expected: Provenance) -> bool:
    try:
        require_trainable(record.provenance, expected.to_dict())
    except ValueError:
        return False
    return True


def require_match(record: RunRecord, expected: Provenance) -> None:
    differences = [
        field
        for field in ("model_id", "prompt_hash", "schema_hash", "benchmark_sha256")
        if getattr(Provenance.from_dict(record.provenance), field)
        != getattr(expected, field)
    ]
    if differences:
        raise RunRecordMismatch(
            f"run record {record.name!r} disagrees with the report: " + ", ".join(differences)
        )


def cell(
    value: Any,
    record: RunRecord | None,
    expected: Provenance,
    *,
    formatter: str = "percent",
) -> str:
    """Render a metric cell only when a matching run record backs it."""
    if record is None or not matches(record, expected):
        return "unverified"
    if value is None:
        return "--"
    if formatter == "percent":
        return f"{float(value):.1%}"
    if formatter == "float":
        return f"{float(value):.1f}"
    return str(value)
