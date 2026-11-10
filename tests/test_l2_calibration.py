"""L1's fault behaviour is an assumption until the live stack agrees with it.

These tests pin the two halves of that comparison: the L1 reference rates are
derived from the configuration (so they cannot drift from the code that builds
the scenarios), and a family is only ever called calibrated when enough
measurements back it and their interval actually contains the L1 value.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_env.faults import FaultType
from agent_env.l2_calibration import (
    PROGRAMMES,
    L2Measurement,
    l1_reference_rates,
    load_measurements,
    measurements_to_manifest,
    plan,
    write_manifest,
)


def test_every_family_has_a_programme() -> None:
    assert set(PROGRAMMES) == set(FaultType)
    for programme in PROGRAMMES.values():
        assert programme.question
        assert programme.success_event
        assert programme.steps


def test_the_l1_rates_are_derived_from_the_configuration() -> None:
    references = l1_reference_rates()
    # A navigation timeout is configured to happen once with probability 2/3
    # (the draw list is [1, 1, 2]).
    assert abs(references[FaultType.NAV_TIMEOUT].rate - 2 / 3) < 0.02
    # A grasp failure has the same shape.
    assert abs(references[FaultType.GRASP_FAILURE].rate - 2 / 3) < 0.02
    # A re-perceive fixes a low-confidence detection half the time.
    assert abs(references[FaultType.LOW_CONFIDENCE].rate - 0.5) < 0.02
    # A face mismatch is a transient glitch about a third of the time.
    assert abs(references[FaultType.IDENTITY_MISMATCH].rate - 0.35) < 0.02


def test_the_l1_rates_do_not_move_between_runs() -> None:
    first = l1_reference_rates()
    second = l1_reference_rates()
    assert {family: reference.rate for family, reference in first.items()} == {
        family: reference.rate for family, reference in second.items()
    }


def test_the_deterministic_families_are_marked_as_such() -> None:
    references = l1_reference_rates()
    assert references[FaultType.VISUAL_OCCLUSION].rate == 1.0
    assert "reposition" in references[FaultType.VISUAL_OCCLUSION].source
    assert references[FaultType.TOOL_MISSING].rate == 1.0


def test_an_unmeasured_family_is_never_calibrated() -> None:
    manifest = measurements_to_manifest([], seed=1)
    assert not manifest["all_calibrated"]
    for family in manifest["families"]:
        assert not family["calibrated"]
        assert family["reason"] == "not_measured"
        assert family["samples"] == 0


def test_fewer_than_thirty_samples_is_never_calibrated() -> None:
    measurements = [
        L2Measurement(family=FaultType.NAV_TIMEOUT.value, successes=29, samples=29),
    ]
    manifest = measurements_to_manifest(measurements, seed=1)
    entry = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert not entry["calibrated"]
    assert entry["reason"] == "insufficient_samples"


def test_a_matching_measurement_calibrates_the_family() -> None:
    references = l1_reference_rates()
    rate = references[FaultType.NAV_TIMEOUT].rate
    samples = 30
    successes = round(rate * samples)
    measurements = [
        L2Measurement(family=FaultType.NAV_TIMEOUT.value, successes=successes, samples=samples),
    ]
    manifest = measurements_to_manifest(measurements, seed=1)
    entry = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert entry["calibrated"]
    assert entry["reason"] == "ok"
    assert entry["wilson_95"][0] <= entry["l1_rate"] <= entry["wilson_95"][1]


def test_an_interval_that_excludes_the_l1_rate_is_not_calibrated() -> None:
    measurements = [
        L2Measurement(family=FaultType.NAV_TIMEOUT.value, successes=30, samples=30),
    ]
    manifest = measurements_to_manifest(measurements, seed=1)
    entry = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert not entry["calibrated"]
    assert entry["reason"] == "interval_excludes_l1_rate"


def test_the_manifest_records_the_question_and_its_source() -> None:
    manifest = measurements_to_manifest([], seed=7)
    entry = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert "navigate" in entry["question"]
    assert entry["l1_source"]


def test_the_manifest_is_written_and_read_back(tmp_path: Path) -> None:
    manifest = measurements_to_manifest(
        [L2Measurement(family="tool_missing", successes=30, samples=30)],
        seed=3,
    )
    path = write_manifest(manifest, tmp_path / "calibration_v1.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == "calibration_v1"
    assert data["seed"] == 3
    assert len(data["families"]) == len(list(FaultType))


def test_a_measurement_log_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "measurements.json"
    path.write_text(
        json.dumps(
            {
                "measurements": [
                    {"family": "nav_timeout", "successes": 20, "samples": 30, "note": "run 1"}
                ]
            }
        ),
        encoding="utf-8",
    )
    measurements = load_measurements(path)
    assert len(measurements) == 1
    assert measurements[0].rate == 20 / 30
    assert measurements[0].note == "run 1"


def test_the_plan_is_printable_per_family() -> None:
    entries = plan([FaultType.TOOL_MISSING])
    assert len(entries) == 1
    assert entries[0]["family"] == "tool_missing"
    assert entries[0]["steps"]
