"""Datasets: SFT corpus, DPO pairs, and the frozen fault-injection benchmark.

Split isolation is enforced here, not assumed:

* every split draws from a disjoint seed range and a disjoint operator pool,
* every scenario carries a content fingerprint,
* `check_leakage` fails loudly if a benchmark instance ever shows up in training.

A number measured on a benchmark that leaked is worse than no number at all.
"""

from .scenario_bank import (
    BENCHMARK_COMPOSITION,
    BUCKETS,
    ScenarioSpec,
    build_benchmark_suite,
    check_leakage,
    fingerprint,
    load_specs,
    sample_scenarios,
    save_specs,
    split_seed,
)

__all__ = [
    "BENCHMARK_COMPOSITION",
    "BUCKETS",
    "ScenarioSpec",
    "build_benchmark_suite",
    "check_leakage",
    "fingerprint",
    "load_specs",
    "sample_scenarios",
    "save_specs",
    "split_seed",
]
