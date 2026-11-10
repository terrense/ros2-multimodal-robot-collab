"""Fault-rate calibration: L1 configuration against measured L2 behaviour.

The rule this module exists to enforce: a family is only "calibrated" when the
live-stack estimate's confidence interval actually covers the L1 value, and only
when enough samples back it. Otherwise the manifest says so.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping

MIN_SAMPLES = 30
Z_95 = 1.96
MANIFEST_VERSION = "calibration_v1"


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 1.0)
    phat = successes / n
    denominator = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denominator
    margin = (z / denominator) * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def compare_family(
    name: str,
    *,
    l1_rate: float,
    l2_successes: int,
    l2_samples: int,
    min_samples: int = MIN_SAMPLES,
) -> dict:
    low, high = wilson_interval(l2_successes, l2_samples)
    enough = l2_samples >= min_samples
    contains = low <= l1_rate <= high
    return {
        "name": name,
        "l1_rate": round(l1_rate, 4),
        "l2_rate": round(l2_successes / l2_samples, 4) if l2_samples else None,
        "wilson_95": [round(low, 4), round(high, 4)],
        "samples": l2_samples,
        "calibrated": bool(enough and contains),
        "reason": (
            "ok"
            if enough and contains
            else "insufficient_samples"
            if not enough
            else "interval_excludes_l1_rate"
        ),
    }


def build_manifest(
    measurements: Mapping[str, Mapping[str, float]],
    *,
    seed: int,
    generated_at: float | None = None,
) -> dict:
    """``measurements``: family -> {l1_rate, l2_successes, l2_samples}."""
    families = [
        compare_family(
            name,
            l1_rate=float(values["l1_rate"]),
            l2_successes=int(values["l2_successes"]),
            l2_samples=int(values["l2_samples"]),
        )
        for name, values in sorted(measurements.items())
    ]
    return {
        "version": MANIFEST_VERSION,
        "seed": seed,
        "generated_at": time.time() if generated_at is None else generated_at,
        "min_samples": MIN_SAMPLES,
        "families": families,
        "all_calibrated": all(family["calibrated"] for family in families),
    }
