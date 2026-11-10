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
from typing import Any, Mapping

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
