"""The calibration runner: what it measures, and what it refuses to measure.

Four failure modes matter more than the probes themselves.

    A family whose scene is not arranged must not become a sample. Filling the
    gap with the L1 value would turn an assumption back into an assumption --
    one wearing a measurement's clothes.

    A probe that came back clean has measured the healthy path, not a recovered
    fault. Counting it as a failure of recovery would blame the system for a
    fault that never happened.

    A probe that hard-codes the tool name measures a tool the stack does not
    have. Against a stub detector publishing `hex_key_3mm`, a probe asking for
    `wrench` reports 0% recovery for a fault the robot never encountered -- and
    that is exactly what happened the first time these ran against a live stack.

    Asking whether a family *can* be measured must not move the robot. That is
    why the reason list is built from the probes' own annotations, not by
    running them.
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
    """A stack whose answers cycle through a per-capability script.

    Cycling rather than consuming matters: a probe repeats, so every sample
    must see the same fault pattern. A consuming script would let the second
    sample see the clean path and the probe would be measuring nothing.
    """

    def __init__(self, script: dict[str, list[str]] | None = None) -> None:
        self.script = {name: list(statuses) for name, statuses in (script or {}).items()}
        self.calls: list[tuple[str, dict]] = []
        self._counts: dict[str, int] = {}

    def call(self, capability: str, arguments: dict):
        self.calls.append((capability, dict(arguments)))
        statuses = self.script.get(capability)
        if not statuses:
            return _Receipt("completed")
        index = self._counts.get(capability, 0)
        self._counts[capability] = index + 1
        return _Receipt(statuses[index % len(statuses)])

    @staticmethod
    def receipt_status(receipt):
        return receipt.execution_status


def _runner(stack, samples: int = 3, **kwargs) -> CalibrationRunner:
    return CalibrationRunner(stack, min_samples=samples, **kwargs)


# ---------------------------------------------------------------- the probes


def test_a_navigation_timeout_is_measured_by_retrying_the_same_station() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["unknown", "completed"]})
    measurement = _runner(stack).measure(FaultType.NAV_TIMEOUT, samples=1)
    assert measurement is not None
    assert measurement.successes == 1
    stations = [arguments["station_id"] for _capability, arguments in stack.calls]
    assert len(set(stations)) == 1, "both attempts must target the same station"


def test_a_navigation_that_never_times_out_is_not_a_measurement() -> None:
    """A clean navigation says nothing about recovering from a timeout."""
    stack = FakeStack({"navigation.navigate_to_station": ["completed"]})
    assert _runner(stack).measure(FaultType.NAV_TIMEOUT, samples=1) is None


def test_a_stack_that_is_not_answering_is_not_measured() -> None:
    stack = FakeStack({"system-state.query_state": ["failed"]})
    assert _runner(stack).measure(FaultType.LOW_CONFIDENCE, samples=2) is None


def test_the_grasp_probe_uses_the_configured_tool_not_a_constant() -> None:
    """The bug this test exists for: a hard-coded name measured nothing."""
    stack = FakeStack({"manipulation.pick_and_place": ["unknown", "completed"]})
    runner = _runner(stack, tool_id="hex_key_3mm")
    measurement = runner.measure(FaultType.GRASP_FAILURE, samples=1)
    assert measurement is not None
    tools = {arguments["tool_id"] for _capability, arguments in stack.calls}
    assert tools == {"hex_key_3mm"}


def test_the_grasp_probe_still_works_for_a_configured_tool() -> None:
    stack = FakeStack({"manipulation.pick_and_place": ["unknown", "completed"]})
    runner = _runner(stack, tool_id="wrench")
    assert runner.measure(FaultType.GRASP_FAILURE, samples=1) is not None
    assert {arguments["tool_id"] for _c, arguments in stack.calls} == {"wrench"}


def test_an_identity_check_that_passes_first_time_is_not_a_mismatch() -> None:
    """A real operator id would verify immediately; the probe must not count that."""
    stack = FakeStack({"perception.verify_operator": ["completed"]})
    assert _runner(stack).measure(FaultType.IDENTITY_MISMATCH, samples=1) is None


def test_an_identity_mismatch_is_produced_with_a_stranger() -> None:
    stack = FakeStack({"perception.verify_operator": ["rejected", "completed"]})
    runner = _runner(stack, operator_id="operator_001")
    measurement = runner.measure(FaultType.IDENTITY_MISMATCH, samples=1)
    assert measurement is not None
    operators = {arguments["operator_id"] for _c, arguments in stack.calls}
    assert operators == {"operator_001_absent"}
    assert "operator_001" not in operators


# ------------------------------------------------- what it refuses to measure


def test_a_family_that_needs_a_scene_is_not_measured() -> None:
    """An occlusion cannot be produced by a skill call, so it is not a sample."""
    stack = FakeStack()
    assert _runner(stack).measure(FaultType.VISUAL_OCCLUSION, samples=5) is None
    assert stack.calls == []


def test_asking_why_a_family_was_not_measured_does_not_dispatch_everything() -> None:
    """`reason_unmeasured` may probe, but a scene-only family must not be run."""
    stack = FakeStack()
    unmeasured = _runner(stack).reason_unmeasured([FaultType.VISUAL_OCCLUSION])
    assert stack.calls == [], "a question about measurability must not move the robot"
    assert unmeasured[0]["family"] == "visual_occlusion"
    assert unmeasured[0]["reason"]


def test_the_three_reasons_are_kept_apart() -> None:
    """Scene, stack and clean-attempt are different problems with different fixes."""
    scene = _runner(FakeStack()).reason_unmeasured([FaultType.VISUAL_OCCLUSION])
    assert scene[0]["reason"].startswith("scene not arranged")

    # The first attempt succeeding means the fault never happened. A *rejected*
    # first attempt is different: that is the fault occurring, and the probe
    # goes on to ask whether a retry recovers.
    clean = _runner(
        FakeStack({"navigation.navigate_to_station": ["completed"]})
    ).reason_unmeasured([FaultType.NAV_TIMEOUT])
    assert clean[0]["reason"].startswith("no fault produced")

    clean_identity = _runner(
        FakeStack({"perception.verify_operator": ["completed"]})
    ).reason_unmeasured([FaultType.IDENTITY_MISMATCH])
    assert clean_identity[0]["reason"].startswith("no fault produced")


def test_a_rejected_first_attempt_is_a_fault_not_a_clean_path() -> None:
    """A rejected check is the mismatch happening; the probe must keep going."""
    stack = FakeStack({"perception.verify_operator": ["rejected", "rejected"]})
    measurement = _runner(stack).measure(FaultType.IDENTITY_MISMATCH, samples=1)
    assert measurement is not None
    assert measurement.successes == 0
    assert len(stack.calls) == 2, "both attempts should have run"


def test_only_two_families_can_produce_their_own_fault() -> None:
    """The list is short on purpose: every other family needs the world arranged.

    `low_confidence` used to be here with a probe that asked `query_state`
    twice. That measures liveness, and reporting it as a confidence-recovery
    rate produced a calibration verdict that looked real and was not.

    `grasp_failure` has a real probe rather than a scene stub, but that probe
    refuses when its fault does not occur -- which on this stack is always,
    because the simulated arm never fails a grasp.
    """
    from agent_env.calibration_runner import PROBES
    from agent_env.faults import FaultType

    self_producing = {
        family
        for family, probe in PROBES.items()
        if getattr(probe, "scene_required", None) is None
    }
    assert self_producing == {
        FaultType.NAV_TIMEOUT,
        FaultType.IDENTITY_MISMATCH,
        FaultType.GRASP_FAILURE,
    }


def test_a_scene_family_cannot_be_measured_without_a_scene_driver() -> None:
    runner = _runner(FakeStack())
    for family in (FaultType.LOW_CONFIDENCE, FaultType.TOOL_MISSING):
        assert runner.measure(family) is None
        reason = runner.reason_unmeasured([family])[0]["reason"]
        assert reason.startswith("scene not arranged"), family


# ------------------------------------------------------------- the manifest


def test_an_unmeasured_family_reaches_the_manifest_as_not_measured() -> None:
    """The whole chain: runner -> measurement log -> manifest."""
    stack = FakeStack({"navigation.navigate_to_station": ["unknown", "completed"]})
    measurements = _runner(stack).run([FaultType.NAV_TIMEOUT])
    manifest = measurements_to_manifest(measurements, seed=7)

    measured = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert measured["samples"] == 3

    occlusion = next(f for f in manifest["families"] if f["name"] == "visual_occlusion")
    assert occlusion["reason"] == "not_measured"
    assert not occlusion["calibrated"]
    assert not manifest["all_calibrated"]


def test_fewer_samples_than_the_minimum_is_never_calibrated() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["unknown", "completed"]})
    measurements = _runner(stack, samples=2).run([FaultType.NAV_TIMEOUT])
    assert measurements[0].samples == 2
    manifest = measurements_to_manifest(measurements, seed=7)
    entry = next(f for f in manifest["families"] if f["name"] == "nav_timeout")
    assert not entry["calibrated"]
    assert entry["reason"] == "insufficient_samples"


def test_the_measurement_log_has_the_shape_the_manifest_reader_expects() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["unknown", "completed"]})
    rows = measurements_to_rows(_runner(stack).run([FaultType.NAV_TIMEOUT]))
    assert rows[0]["family"] == "nav_timeout"
    assert {"successes", "samples", "note"} <= set(rows[0])


# ---------------------------------------------------------------- rendering


def test_the_runner_reports_what_it_did_and_did_not_do() -> None:
    stack = FakeStack({"navigation.navigate_to_station": ["unknown", "completed"]})
    runner = _runner(stack)
    text = describe(runner.run([FaultType.NAV_TIMEOUT]))
    assert "nav_timeout" in text and "1.000" in text

    unmeasured = summarise_skipped(runner.reason_unmeasured([FaultType.VISUAL_OCCLUSION]))
    assert "visual_occlusion" in unmeasured
    assert "not measured" in unmeasured


def test_every_family_has_an_entry_in_the_probe_table() -> None:
    assert set(PROBES) == set(FaultType)


def test_every_probe_accepts_the_runner() -> None:
    """The probes take (stack, scene); a stale signature would fail here, not at
    the first real run."""
    import inspect

    for family, probe in PROBES.items():
        parameters = list(inspect.signature(probe).parameters)
        assert parameters == ["stack", "scene"], f"{family.value}: {parameters}"
