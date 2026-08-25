"""Scenario sampling with enforced train / dev / benchmark isolation.

Three mechanisms, all checkable:

    seed range      each split owns a disjoint block of episode seeds
    operator pool   each split owns a disjoint set of operator ids, so no exact
                    mission string can appear in two splits
    fingerprint     sha1 over the full scenario content (tool, station, operator,
                    confirmation flag, fault types *and* their sampled params)

`check_leakage` intersects fingerprints and is meant to be run before every
benchmark evaluation, not once at setup time.

The benchmark itself is a frozen file (`benchmarks/fault_injection_v1.jsonl`),
regenerable byte-for-byte from `BENCHMARK_SEED` and verified by a manifest
checksum, so results across checkpoints are comparable.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from agent_env.faults import COMPATIBLE_PAIRS, FaultSpec, FaultType, default_params
from agent_env.l1_sim import TOOLS, WORKSTATIONS, Scenario

#: Disjoint seed blocks. Episode seeds never collide across splits.
SEED_BLOCKS: dict[str, int] = {
    "train": 1_000_000,
    "dev": 5_000_000,
    "benchmark": 9_000_000,
}

#: Disjoint operator pools, so a benchmark mission string cannot be memorised from training.
OPERATOR_POOLS: dict[str, list[str]] = {
    "train": [f"operator_{index:03d}" for index in range(1, 21)],
    "dev": [f"operator_{index:03d}" for index in range(51, 61)],
    "benchmark": [f"operator_{index:03d}" for index in range(101, 121)],
}

#: Seed of the frozen benchmark. Changing it invalidates every published number.
BENCHMARK_SEED = 20260724

BUCKETS = ("clean", "navigation", "perception", "manipulation", "interaction", "mixed")

_FAULT_BUCKET: dict[FaultType, str] = {
    FaultType.NAV_TIMEOUT: "navigation",
    FaultType.VISUAL_OCCLUSION: "perception",
    FaultType.LOW_CONFIDENCE: "perception",
    FaultType.TOOL_MISSING: "perception",
    FaultType.GRASP_FAILURE: "manipulation",
    FaultType.TOOL_DROP: "manipulation",
    FaultType.IDENTITY_MISMATCH: "interaction",
    FaultType.USER_CANCEL: "interaction",
    FaultType.NO_PROGRESS: "interaction",
}

#: 400 held-out episodes. Per-fault counts are fixed so bucket-level numbers stay
#: comparable between checkpoints; `mixed` spreads evenly over the compatible pairs.
BENCHMARK_COMPOSITION: dict[str, int] = {
    "clean": 40,
    FaultType.NAV_TIMEOUT.value: 60,
    FaultType.VISUAL_OCCLUSION.value: 40,
    FaultType.LOW_CONFIDENCE.value: 30,
    FaultType.TOOL_MISSING.value: 30,
    FaultType.GRASP_FAILURE.value: 50,
    FaultType.TOOL_DROP.value: 30,
    FaultType.IDENTITY_MISMATCH.value: 20,
    FaultType.USER_CANCEL.value: 20,
    FaultType.NO_PROGRESS.value: 20,
    "mixed": 60,
}

#: Training-time fault mix (none : single : double), matching the plan's 3:5:2.
TRAIN_MIX = (0.3, 0.5, 0.2)


def bucket_of(faults: Sequence[FaultSpec]) -> str:
    if not faults:
        return "clean"
    if len(faults) > 1:
        return "mixed"
    return _FAULT_BUCKET[faults[0].fault]


def split_seed(split: str, index: int) -> int:
    """Episode seed for `index` inside `split`. Blocks never overlap."""
    try:
        block = SEED_BLOCKS[split]
    except KeyError as exc:  # pragma: no cover - defensive
        raise KeyError(f"unknown split {split!r}; known: {sorted(SEED_BLOCKS)}") from exc
    if index < 0 or index >= 1_000_000:
        raise ValueError(f"index {index} outside the {split} seed block")
    return block + index


def fingerprint(scenario: Scenario | dict[str, Any]) -> str:
    """Stable content hash of a scenario instance, fault parameters included."""
    payload = scenario.to_dict() if isinstance(scenario, Scenario) else dict(scenario)
    canonical = {
        "tool_id": payload.get("tool_id"),
        "target_station": payload.get("target_station"),
        "operator_id": payload.get("operator_id"),
        "require_confirmation": payload.get("require_confirmation"),
        "faults": sorted(
            (
                {"fault": item.get("fault"), "params": dict(item.get("params") or {})}
                for item in payload.get("faults") or []
            ),
            key=lambda item: str(item["fault"]),
        ),
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ScenarioSpec:
    """One reproducible episode: scenario content + the seed that drives the env."""

    scenario_id: str
    split: str
    bucket: str
    seed: int
    scenario: dict[str, Any]

    def to_scenario(self) -> Scenario:
        payload = self.scenario
        return Scenario(
            tool_id=payload["tool_id"],
            target_station=payload["target_station"],
            operator_id=payload["operator_id"],
            faults=[FaultSpec.from_dict(item) for item in payload.get("faults") or []],
            require_confirmation=bool(payload.get("require_confirmation", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "split": self.split,
            "bucket": self.bucket,
            "seed": self.seed,
            "scenario": self.scenario,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScenarioSpec":
        return cls(
            scenario_id=str(data["scenario_id"]),
            split=str(data["split"]),
            bucket=str(data["bucket"]),
            seed=int(data["seed"]),
            scenario=dict(data["scenario"]),
        )


def _make_spec(
    split: str,
    index: int,
    faults: list[FaultSpec],
    rng: random.Random,
    seen: set[str] | None = None,
    attempts: int = 64,
) -> ScenarioSpec:
    """Build one spec; with `seen`, resample the mission until the content is unique.

    Only the mission fields are resampled -- the fault parameters are already
    drawn, so the requested bucket composition is never disturbed.
    """
    for _ in range(attempts):
        scenario = Scenario(
            tool_id=rng.choice(TOOLS),
            target_station=rng.choice(WORKSTATIONS),
            operator_id=rng.choice(OPERATOR_POOLS[split]),
            faults=faults,
            require_confirmation=rng.random() < 0.7,
        )
        payload = scenario.to_dict()
        if seen is None or fingerprint(payload) not in seen:
            break
    if seen is not None:
        seen.add(fingerprint(payload))
    return ScenarioSpec(
        scenario_id=f"{split}-{index:06d}-{fingerprint(payload)}",
        split=split,
        bucket=bucket_of(faults),
        seed=split_seed(split, index),
        scenario=payload,
    )


def sample_scenarios(
    split: str,
    count: int,
    *,
    rng_seed: int | None = None,
    mix: tuple[float, float, float] = TRAIN_MIX,
    fault: FaultType | None = None,
    start_index: int = 0,
) -> list[ScenarioSpec]:
    """Sample training/dev scenarios with a none:single:double fault mix.

    `start_index` shifts both the episode seeds and the RNG stream, so successive
    RL iterations draw fresh scenarios instead of replaying the same ones.
    """
    if split not in SEED_BLOCKS:
        raise KeyError(f"unknown split {split!r}")
    base_seed = rng_seed if rng_seed is not None else SEED_BLOCKS[split]
    rng = random.Random(base_seed + start_index)
    none_weight, single_weight, _ = mix
    specs: list[ScenarioSpec] = []
    for offset in range(count):
        index = start_index + offset
        if fault is not None:
            faults = [FaultSpec(fault, default_params(fault, rng))]
        else:
            roll = rng.random()
            if roll < none_weight:
                faults = []
            elif roll < none_weight + single_weight:
                chosen = rng.choice(list(FaultType))
                faults = [FaultSpec(chosen, default_params(chosen, rng))]
            else:
                first, second = rng.choice(COMPATIBLE_PAIRS)
                faults = [
                    FaultSpec(first, default_params(first, rng)),
                    FaultSpec(second, default_params(second, rng)),
                ]
        specs.append(_make_spec(split, index, faults, rng))
    return specs


def build_benchmark_suite(seed: int = BENCHMARK_SEED) -> list[ScenarioSpec]:
    """The frozen held-out suite: 400 episodes with a fixed per-bucket composition."""
    rng = random.Random(seed)
    specs: list[ScenarioSpec] = []
    seen: set[str] = set()  # the suite holds 400 distinct scenarios, not 400 draws
    index = 0

    for _ in range(BENCHMARK_COMPOSITION["clean"]):
        specs.append(_make_spec("benchmark", index, [], rng, seen))
        index += 1

    for fault in FaultType:
        for _ in range(BENCHMARK_COMPOSITION[fault.value]):
            specs.append(_make_spec("benchmark", index, [FaultSpec(fault, default_params(fault, rng))], rng, seen))
            index += 1

    per_pair, remainder = divmod(BENCHMARK_COMPOSITION["mixed"], len(COMPATIBLE_PAIRS))
    for pair_index, (first, second) in enumerate(COMPATIBLE_PAIRS):
        count = per_pair + (1 if pair_index < remainder else 0)
        for _ in range(count):
            faults = [
                FaultSpec(first, default_params(first, rng)),
                FaultSpec(second, default_params(second, rng)),
            ]
            specs.append(_make_spec("benchmark", index, faults, rng, seen))
            index += 1
    return specs


def check_leakage(train: Iterable[ScenarioSpec], benchmark: Iterable[ScenarioSpec]) -> dict[str, Any]:
    """Intersect two split's fingerprints, seeds and operator pools."""
    train = list(train)
    benchmark = list(benchmark)
    train_prints = {fingerprint(spec.scenario) for spec in train}
    bench_prints = {fingerprint(spec.scenario) for spec in benchmark}
    shared_seeds = {spec.seed for spec in train} & {spec.seed for spec in benchmark}
    shared_operators = {spec.scenario["operator_id"] for spec in train} & {
        spec.scenario["operator_id"] for spec in benchmark
    }
    shared_prints = train_prints & bench_prints
    return {
        "train_episodes": len(train),
        "benchmark_episodes": len(benchmark),
        "shared_fingerprints": sorted(shared_prints),
        "shared_seeds": sorted(shared_seeds),
        "shared_operators": sorted(shared_operators),
        "clean": not (shared_prints or shared_seeds or shared_operators),
    }


def save_specs(path: Path | str, specs: Sequence[ScenarioSpec]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for spec in specs:
            handle.write(json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return path


def load_specs(path: Path | str) -> list[ScenarioSpec]:
    specs: list[ScenarioSpec] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                specs.append(ScenarioSpec.from_dict(json.loads(line)))
    return specs


def checksum(path: Path | str) -> str:
    """sha256 of a spec file; the manifest pins it so a silent edit is detectable."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "BENCHMARK_COMPOSITION",
    "BENCHMARK_SEED",
    "BUCKETS",
    "OPERATOR_POOLS",
    "SEED_BLOCKS",
    "TRAIN_MIX",
    "ScenarioSpec",
    "bucket_of",
    "build_benchmark_suite",
    "check_leakage",
    "checksum",
    "fingerprint",
    "load_specs",
    "sample_scenarios",
    "save_specs",
    "split_seed",
]
