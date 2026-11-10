"""The calibration runner: what it measures, and what it refuses to measure.

Two failure modes matter more than the probes themselves.

    A family whose scene is not arranged must not become a sample. Filling the
    gap with the L1 value would turn an assumption back into an assumption --
    one wearing a measurement's clothes.

    Asking whether a family *can* be measured must not move the robot. That is
    why `skipped()` inspects the probes instead of running them.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_env.calibration_runner import (
    PROBES,
    CalibrationRunner,
    describe,
    measurements_to_rows,
    summarise_skipped,
)
from agent_env.faults import FaultType
from agent_env.l2_calibration import measurements_to_manifest


@dataclass
class _Receipt:
    execution_status: str


class FakeStack:
    """A stack that answers a fixed sequence of statuses per capability."""

    def __init__(self, script: dict[str, list[str]]) -> None:
        self.script = {name: list(statuses) for name, statuses in script.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, capability: str, arguments: dict):
        self.calls.append((capability, dict(arguments)))
        statuses = self.script.get(capability) or ["completed"]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return _Receipt(status)

    @staticmethod
    def receipt_status(receipt):
        return receipt.execution_status


def _runner(stack, samples=3) -> CalibrationRunner:
    return CalibrationRunner(stack, min_samples=samples)


# ---------------------------------------------------------------- the probes


def test_a_navigation_timeout_is_measured_by_retrying_the_same_station() -> None:
    stack = FakeStack(
        {"navigation.navigate_to_station": ["failed", "completed"]},
    )
    measurement = _runner(stack).measure(FaultType.NAV_TIMEOUT, samples=1)
    assert measurement is not None
    assert measurement.successes == 1
    assert stack.calls[0][0] == "navigation.navigate_to_station"
    assert stack.calls[0][1]["station_id"] == stack.calls[1][1]["station_id"]


def test_a_navigation_that_never_recovers_is_a_failed_sample() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["failed"]})
    measurement = _runner(stack).measure(FaultType.NAV_TIMEOUT, samples=2)
    assert measurement is not None
    assert measurement.successes == 0
    assert measurement.samples == 2


def test_the_sample_count_is_the_attempt_count() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["failed", "completed"]})
    measurement = _runner(stack).measure(FaultType.NAV_TIMEOUT, samples=5)
    assert measurement is not None
    assert measurement.samples == 5
    assert measurement.successes == 5


# ------------------------------------------------- what it refuses to measure


def test_a_family_that_needs_a_scene_is_not_measured() -> None:
    """An occlusion cannot be produced by a skill call, so it is not a sample."""
    stack = FakeStack({})
    measurement = _runner(stack).measure(FaultType.VISUAL_OCCLUSION, samples=5)
    assert measurement is None
    assert stack.calls == []


def test_a_stack_that_is_not_answering_is_not_measured() -> None:
    stack = FakeStack({"system-state.query_state": ["failed"]})
    measurement = _runner(stack).measure(FaultType.LOW_CONFIDENCE, samples=2)
    assert measurement is None


def test_asking_what_cannot_be_measured_does_not_dispatch_anything() -> None:
    """`skipped()` must answer from the probes, not by running them."""
    stack = FakeStack({})
    skipped = _runner(stack).skipped()
    assert stack.calls == [], "a question about measurability must not move the robot"
    families = {item["family"] for item in skipped}
    assert families == {"visual_occlusion", "tool_drop", "no_progress", "user_cancel"}
    for item in skipped:
        assert item["reason"]


def test_the_runnable_families_are_not_listed_as_skipped() -> None:
    stack = FakeStack({})
    skipped = {item["family"] for item in _runner(stack).skipped()}
    assert "nav_timeout" not in skipped
    assert "grasp_failure" not in skipped


# ------------------------------------------------------------- the manifest


def test_an_unmeasured_family_reaches_the_manifest_as_not_measured() -> None:
    """The whole chain: runner → measurement log → manifest."""
    stack = FakeStack({"navigation.navigate_to_station": ["failed", "completed"]})
    measurements = _runner(stack).run([FaultType.NAV_TIMEOUT])
    manifest = measurements_to_manifest(measurements, seed=7)

    measured = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert measured["samples"] == 3

    occlusion = next(f for f in manifest["families"] if f["name"] == "visual_occlusion")
    assert occlusion["reason"] == "not_measured"
    assert not occlusion["calibrated"]
    assert not manifest["all_calibrated"]


def test_fewer_samples_than_the_minimum_is_never_calibrated() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["failed", "completed"]})
    measurements = _runner(stack, samples=2).run([FaultType.NAV_TIMEOUT])
    assert measurements[0].samples == 2
    manifest = measurements_to_manifest(measurements, seed=7)
    entry = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert not entry["calibrated"]
    assert entry["reason"] == "insufficient_samples"


def test_the_measurement_log_has_the_shape_the_manifest_reader_expects() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["failed", "completed"]})
    measurements = _runner(stack).run([FaultType.NAV_TIMEOUT])
    rows = measurements_to_rows(measurements)
    assert rows[0]["family"] == "nav_timeout"
    assert {"successes", "samples", "note"} <= set(rows[0])


# ---------------------------------------------------------------- rendering


def test_the_runner_reports_what_it_did_and_did_not_do() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["failed", "completed"]})
    runner = _runner(stack)
    text = describe(runner.run([FaultType.NAV_TIMEOUT]))
    assert "nav_timeout" in text and "1.000" in text

    skipped = summarise_skipped(runner.skipped([FaultType.VISUAL_OCCLUSION]))
    assert "visual_occlusion" in skipped
    assert "not measured" in skipped


def test_every_family_has_an_entry_in_the_probe_table() -> None:
    assert set(PROBES) == set(FaultType)
