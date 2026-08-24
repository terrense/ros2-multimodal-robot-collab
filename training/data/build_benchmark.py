#!/usr/bin/env python3
"""Generate (or verify) the frozen fault-injection benchmark.

    python -m training.data.build_benchmark --verify

400 held-out episodes with a fixed per-fault composition, drawn from the
benchmark seed block and operator pool so nothing can overlap with training.
The file is regenerable byte-for-byte from `BENCHMARK_SEED`; the manifest pins a
checksum so an accidental edit is caught instead of quietly changing every
number that was ever reported against it.

`--verify` additionally runs the scripted expert over the suite. A benchmark the
expert cannot solve measures the environment, not the policy, so this must stay
at 100% recovery.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from agent_env.protocol import ActionRegistry

from .scenario_bank import (
    BENCHMARK_COMPOSITION,
    BENCHMARK_SEED,
    OPERATOR_POOLS,
    ScenarioSpec,
    build_benchmark_suite,
    checksum,
    load_specs,
    save_specs,
)

DEFAULT_OUT = Path("benchmarks/fault_injection_v1.jsonl")
MANIFEST = Path("benchmarks/manifest.json")


def write_manifest(path: Path, specs: list[ScenarioSpec], seed: int, manifest_path: Path = MANIFEST) -> dict:
    manifest = {
        "name": "fault_injection_v1",
        "description": "Frozen held-out fault-injection benchmark for the tool-delivery agent.",
        "seed": seed,
        "episodes": len(specs),
        "composition": dict(BENCHMARK_COMPOSITION),
        "buckets": dict(sorted(Counter(spec.bucket for spec in specs).items())),
        "operator_pool": OPERATOR_POOLS["benchmark"],
        "file": path.as_posix(),
        "sha256": checksum(path),
        "regenerate": "python -m training.data.build_benchmark",
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return manifest


def verify_with_expert(specs: list[ScenarioSpec]) -> bool:
    """Run the scripted expert over the suite and report per-bucket recovery."""
    from training.envs import run_episode, scripted_policy

    registry = ActionRegistry.load()
    rows: dict[str, list[dict]] = defaultdict(list)
    for spec in specs:
        record = run_episode(
            scripted_policy(),
            spec.to_scenario(),
            seed=spec.seed,
            registry=registry,
            source="benchmark_verify",
            episode_id=spec.scenario_id,
        )
        rows[spec.bucket].append(record.summary)

    print(f"\n{'bucket':<16}{'n':>5}{'recovered':>11}{'invalid':>9}{'safety':>8}{'avg_steps':>11}")
    ok = True
    for bucket in sorted(rows):
        summaries = rows[bucket]
        count = len(summaries)
        recovered = sum(1 for item in summaries if item["recovered"]) / count
        invalid = sum(item["invalid_actions"] for item in summaries)
        safety = sum(item["safety_violations"] for item in summaries)
        steps = sum(item["steps"] for item in summaries) / count
        print(f"{bucket:<16}{count:>5}{recovered:>10.0%}{invalid:>9}{safety:>8}{steps:>11.1f}")
        if recovered < 1.0 or invalid > 0:
            ok = False
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=BENCHMARK_SEED)
    parser.add_argument("--verify", action="store_true", help="Run the scripted expert over the suite.")
    parser.add_argument("--check", action="store_true", help="Only check that the file on disk matches the seed.")
    args = parser.parse_args()

    specs = build_benchmark_suite(args.seed)

    if args.check:
        if not args.out.exists():
            raise SystemExit(f"missing benchmark file: {args.out}")
        on_disk = load_specs(args.out)
        drift = [
            (a.scenario_id, b.scenario_id)
            for a, b in zip(on_disk, specs)
            if a.scenario_id != b.scenario_id or a.seed != b.seed
        ]
        if len(on_disk) != len(specs) or drift:
            raise SystemExit(f"benchmark drift: {len(drift)} mismatched specs (regenerate or restore the file)")
        print(f"benchmark matches seed {args.seed}: {len(on_disk)} episodes, sha256={checksum(args.out)[:16]}...")
        return

    save_specs(args.out, specs)
    manifest = write_manifest(args.out, specs, args.seed)
    print(f"wrote {len(specs)} episodes -> {args.out}")
    for bucket, count in manifest["buckets"].items():
        print(f"  {bucket:<14}{count:>5}")
    print(f"sha256: {manifest['sha256']}")

    if args.verify:
        ok = verify_with_expert(specs)
        print("\nexpert verification:", "PASS" if ok else "FAIL (benchmark is not solvable by the scripted expert)")
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
