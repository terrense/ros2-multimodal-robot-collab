"""Provenance for training samples and runs.

Every sample and every reported number must name the exact model, prompt,
action schema and benchmark that produced it. Records are additive: v1 records
have no ``schema_version`` and keep parsing; v2 records carry it explicitly.

Hashes are content hashes over file bytes, so a prompt or schema edit changes
them without anyone remembering to bump a version.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 2
SHORT_HASH_CHARS = 16


def sha256_full(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def short_hash(data: bytes) -> str:
    return sha256_full(data)[:SHORT_HASH_CHARS]


def hash_text(text: str) -> str:
    return short_hash(text.encode("utf-8"))


def hash_json(value: Any) -> str:
    return short_hash(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def hash_file(path: Path | str) -> str:
    return sha256_full(Path(path).read_bytes())


@dataclass(frozen=True)
class Provenance:
    model_id: str
    prompt_hash: str
    schema_hash: str
    benchmark_name: str
    benchmark_sha256: str
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "prompt_hash": self.prompt_hash,
            "schema_hash": self.schema_hash,
            "benchmark": {
                "name": self.benchmark_name,
                "sha256": self.benchmark_sha256,
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Provenance":
        benchmark = data.get("benchmark") or {}
        return cls(
            model_id=str(data["model_id"]),
            prompt_hash=str(data["prompt_hash"]),
            schema_hash=str(data["schema_hash"]),
            benchmark_name=str(benchmark.get("name", "unknown")),
            benchmark_sha256=str(benchmark.get("sha256", "")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )

    @classmethod
    def build(
        cls,
        *,
        model_id: str,
        prompt_text: str,
        action_schema: Any,
        benchmark_path: Path | str,
    ) -> "Provenance":
        return cls(
            model_id=model_id,
            prompt_hash=hash_text(prompt_text),
            schema_hash=hash_json(action_schema),
            benchmark_name=Path(benchmark_path).stem,
            benchmark_sha256=hash_file(benchmark_path),
        )


class ProvenanceMismatch(ValueError):
    """Raised when a sample was produced by a different model/prompt/schema/benchmark."""


def mismatches(sample: Mapping[str, Any], reference: Mapping[str, Any]) -> list[str]:
    """Field names that disagree between a sample's and a run's provenance."""
    sample_p = Provenance.from_dict(sample)
    reference_p = Provenance.from_dict(reference)
    differences = []
    for field in ("model_id", "prompt_hash", "schema_hash", "benchmark_sha256"):
        if getattr(sample_p, field) != getattr(reference_p, field):
            differences.append(field)
    return differences


def require_trainable(sample: Mapping[str, Any], reference: Mapping[str, Any]) -> None:
    differences = mismatches(sample, reference)
    if differences:
        raise ProvenanceMismatch(
            "sample provenance disagrees with the run record: " + ", ".join(differences)
        )


def environment_provenance(
    model_id: str,
    benchmark_path: Path | str,
    *,
    prompt_text: str | None = None,
    registry: Any = None,
    catalogue: str = "l1",
) -> Provenance:
    """Provenance for a run that used the live prompt surface and action catalogue.

    The hashes come from the objects the run actually used, not from a copy of
    them: a prompt edit or a schema edit changes the hash, and the report then
    refuses to present a number measured under the old rules as current.

    `catalogue` names which action vocabulary was executed:

    - `l1`   the 13 step-level actions in `agent_env/schemas/agent_skills.json`,
             used by a model policy through the chat surface;
    - `ros2` the capability registry in `skills/*/schema.json`, used when a
             mission is dispatched straight to the stack with no model and
             therefore no prompt at all.

    An L2 mission has no prompt, and hashing a prompt it never used would make
    the run record claim something false. Its provenance carries an empty-prompt
    marker instead, which is why an L1 record and an L2 record can never be
    mistaken for each other.
    """
    from .chat_format import system_prompt

    if catalogue == "ros2":
        from agent_harness.scripts.ros_tool_executor import SkillRegistry

        root = Path(__file__).resolve().parents[1]
        ros_registry = SkillRegistry.load(root / "skills")
        return Provenance.build(
            model_id=model_id,
            prompt_text="",
            action_schema=ros_registry.catalogue(),
            benchmark_path=benchmark_path,
        )
    if catalogue != "l1":
        raise ValueError(f"unknown catalogue {catalogue!r}; expected 'l1' or 'ros2'")

    from agent_env.protocol import ActionRegistry

    actions = registry or ActionRegistry.load()
    prompt = prompt_text if prompt_text is not None else system_prompt(actions)
    schema = {name: actions.spec(name) for name in actions.action_names}
    return Provenance.build(
        model_id=model_id,
        prompt_text=prompt,
        action_schema=schema,
        benchmark_path=benchmark_path,
    )


def record_provenance(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The provenance block of a record, or None for a v1 record.

    Trajectory records carry it at the top level; corpus samples carry it
    inside `meta`, because that is where the dataset builder put it.
    """
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping):
        return dict(provenance)
    meta = record.get("meta")
    if isinstance(meta, Mapping):
        nested = meta.get("provenance")
        if isinstance(nested, Mapping):
            return dict(nested)
    return None


def require_trainable_records(
    records: Iterable[Mapping[str, Any]],
    expected: Provenance,
) -> int:
    """Refuse records whose provenance disagrees with the run record.

    v1 records carry no provenance and are accepted unchanged -- that is what
    keeps old corpora usable. What is refused is a v2 record measured under a
    different prompt or a different action catalogue, because training on it
    would silently mix two distributions.
    """
    checked = 0
    for record in records:
        provenance = record_provenance(record)
        if provenance is None:
            continue
        require_trainable(provenance, expected.to_dict())
        checked += 1
    return checked
