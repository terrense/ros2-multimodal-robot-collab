"""Run records: the evidence a reported number requires.

A metrics file alone is not enough. Every reported cell must be backed by a run
record that names the model, prompt, schema, benchmark, seeds and budgets that
produced it. The report renders `unverified` for anything else.

The record is written by the same code that produced the number:

    python -m training.run_record --name rule_baseline --kind eval \
        --model-id rule_baseline --policy rules \
        --benchmark benchmarks/fault_injection_v1.jsonl \
        --artifact runs/eval/rule_baseline.json --episodes 400 \
        --out results/run_records/rule_baseline.json

so the hashes in it cannot drift from the prompt surface or the action schema
that were actually used.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .provenance import Provenance, environment_provenance, require_trainable


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
    policy: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "provenance": self.provenance,
            "seeds": list(self.seeds),
            "budgets": dict(self.budgets),
            "artifacts": list(self.artifacts),
            "policy": self.policy,
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
            policy=str(data.get("policy", "")),
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


# ------------------------------------------------------------------- authoring


def build_provenance(
    model_id: str,
    benchmark_path: Path | str,
    *,
    catalogue: str = "l1",
) -> Provenance:
    """Provenance taken from the live prompt surface and action schema.

    Hashing the real sources rather than a copy of them is the whole point: a
    prompt edit or a schema edit changes the hash, and the report then refuses
    to present a number measured under the old rules as if it were current.

    `catalogue="ros2"` is for a run with no model behind it — an L2 mission
    dispatched straight to the stack. Its provenance hashes the ROS capability
    registry and carries an empty prompt, because claiming a prompt that was
    never used would be a false record.
    """
    return environment_provenance(model_id, benchmark_path, catalogue=catalogue)


def write_run_record(
    *,
    name: str,
    kind: str,
    model_id: str,
    benchmark: Path | str,
    policy: str = "",
    episodes: int | None = None,
    seeds: list[int] | None = None,
    artifacts: list[str] | None = None,
    catalogue: str = "l1",
    out: Path | str,
) -> RunRecord:
    from agent_env.protocol import MAX_EPISODE_STEPS

    budgets: dict[str, Any] = {"max_turns": MAX_EPISODE_STEPS}
    if episodes is not None:
        budgets["episodes"] = episodes
    record = RunRecord(
        kind=kind,
        name=name,
        provenance=environment_provenance(model_id, benchmark, catalogue=catalogue).to_dict(),
        seeds=list(seeds or []),
        budgets=budgets,
        artifacts=list(artifacts or []),
        policy=policy,
    )
    record.write(out)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="arm name, e.g. rule_baseline")
    parser.add_argument("--kind", default="eval", help="eval | train | ablate")
    parser.add_argument("--model-id", required=True, help="model or policy that produced the number")
    parser.add_argument("--policy", default="", help="the --policy string passed to training.evaluate")
    parser.add_argument("--benchmark", type=Path, default=Path("benchmarks/fault_injection_v1.jsonl"))
    parser.add_argument("--episodes", type=int, default=None)
    parser.add_argument("--seed", type=int, action="append", default=None)
    parser.add_argument("--artifact", action="append", default=None, help="file the number came from")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    record = write_run_record(
        name=args.name,
        kind=args.kind,
        model_id=args.model_id,
        benchmark=args.benchmark,
        policy=args.policy,
        episodes=args.episodes,
        seeds=args.seed,
        artifacts=args.artifact,
        out=args.out,
    )
    print(f"-> {args.out}")
    print(json.dumps(record.provenance, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
