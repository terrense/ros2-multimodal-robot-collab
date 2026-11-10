#!/usr/bin/env python3
"""Calibrate L1's fault behaviour against the live stack.

    python -m agent_env.scripts.calibrate_l2 --plan
    python -m agent_env.scripts.calibrate_l2 --l1
    python -m agent_env.scripts.calibrate_l2 --from-log runs/l2_measurements.json

L1's fault parameters are configured, not measured. This script states what
would have to be observed on the live stack for each family to be called
calibrated, prints the L1 side of that comparison (derived from the
configuration itself), and writes `benchmarks/calibration_v1.json` from the
measurements that were recorded.

The three modes answer three different questions:

    --plan      what would have to be run on the live stack, per family
    --l1        what L1 predicts, and where that prediction comes from
    --from-log  what the live stack was observed to do, and whether the two agree

Nothing here invents a measurement. A family with no recorded samples is
written with `calibrated: false` and the reason `not_measured`, and fewer than
30 samples never counts as calibrated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_env.calibration import MIN_SAMPLES
from agent_env.faults import FaultType
from agent_env.l2_calibration import (
    l1_reference_rates,
    load_measurements,
    measurements_to_manifest,
    plan,
    write_manifest,
)

DEFAULT_MANIFEST = Path("benchmarks/calibration_v1.json")


def print_plan(families: list[FaultType]) -> None:
    for entry in plan(families):
        print(f"[{entry['family']}]")
        print(f"  question:     {entry['question']}")
        print(f"  success when: {entry['success_event']}")
        if entry["scene_setup"]:
            print("  scene setup:")
            for item in entry["scene_setup"]:
                print(f"    - {item}")
        print(f"  steps ({entry['min_samples']} samples minimum):")
        for index, step in enumerate(entry["steps"], start=1):
            print(f"    {index}. {step}")
        print("")


def print_l1(references: dict[FaultType, object]) -> None:
    print(f"{'family':22s} {'rate':>7s}  source")
    for family, reference in references.items():
        print(f"{family.value:22s} {reference.rate:7.3f}  {reference.source}")
        print(f"{'':22s} {'':7s}  {reference.question}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", action="store_true", help="print the live measurement programme")
    parser.add_argument("--l1", action="store_true", help="print the L1 reference rates")
    parser.add_argument("--from-log", type=Path, default=None, help="measurement log to build the manifest from")
    parser.add_argument("--out", type=Path, default=DEFAULT_MANIFEST, help="manifest path")
    parser.add_argument("--seed", type=int, default=20261110, help="seed recorded in the manifest")
    parser.add_argument("--family", action="append", default=None, help="restrict to these fault families")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    args = parser.parse_args()

    families = [FaultType(name) for name in args.family] if args.family else None
    references = l1_reference_rates()

    if args.plan or (not args.l1 and not args.from_log):
        print_plan(families or list(references))
    if args.l1:
        print_l1(references)
    if not args.from_log:
        return

    measurements = load_measurements(args.from_log)
    manifest = measurements_to_manifest(measurements, seed=args.seed, references=references)
    write_manifest(manifest, args.out)
    print(f"-> {args.out}")
    for family in manifest["families"]:
        state = "calibrated" if family["calibrated"] else family["reason"]
        print(
            f"  {family['name']:22s} l1={family['l1_rate']:.3f} "
            f"l2={family['l2_rate']} n={family['samples']} [{state}]"
        )
    print(json.dumps({"all_calibrated": manifest["all_calibrated"]}, indent=2))


if __name__ == "__main__":
    main()
