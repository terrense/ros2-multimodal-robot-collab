"""The named evaluation conditions (Phase 6).

Each condition switches on exactly one more thing than the previous one, so
the difference between two rows of the ablation table is the value of that
thing and nothing else. The tests below check that each switch does what it
says, and -- just as important -- that the default behaviour did not change.

The subtle one is `loop_no_receipts`. Hiding only the injected receipt would
leave the outcome visible on L1, because the observation itself carries
`last_result`. An ablation that measures nothing is worse than no ablation:
it produces a number that looks like a result.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import pytest

from agent_harness.agent.conditions import (
    DEFAULT_CONDITION,
    LOOP_CONDITIONS,
    LOOP_NO_RECEIPTS,
    LOOP_RECEIPTS,
    LOOP_RECEIPTS_FRESH,
    LOOP_RECEIPTS_RECOVERY,
    by_name,
)
from agent_harness.agent.loop import ClosedLoop
from agent_harness.agent.policy import Decide, Finish
from agent_harness.contracts import CapabilityRegistry, ExecutionStatus
from agent_harness.runtime.client import ToolClient
from agent_harness.runtime.fake_backend import FakeBackend
from agent_harness.runtime.snapshots import SnapshotBuilder

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)

ARM = "manipulation.publish_arm_control"
ARM_ARGS = {"command": "arm_start"}


@dataclass
class Clock:
    """A clock that moves.

    The loop's wait-for-a-view loop compares `now()` against a deadline, so a
    clock that never advances makes that loop spin forever. The existing agent
    tests never hit it because their snapshot was always fresh; a stale
    snapshot with the gate on does.
    """

    t: float = 1000.0
    step: float = 0.05

    def __call__(self) -> float:
        value = self.t
        self.t += self.step
        return value


def make_loop(policy, *, condition=None, clock=None, backend=None, payload=None, taken_at=None):
    clock = clock or Clock()
    backend = backend or FakeBackend()
    client = ToolClient(backend, REGISTRY)
    builder = SnapshotBuilder(
        sensor_id="d435",
        calibration_version="calib-test",
        frame_id="map",
        clock=clock,
    )
    body = {"pose": [0.0, 0.0]} if payload is None else dict(payload)

    def observe():
        # `taken_at` is explicit so a test can hand the loop a snapshot that is
        # already old; the builder's own clock would always make it fresh.
        kwargs = {"taken_at": taken_at} if taken_at is not None else {}
        return builder.build(dict(body), state_version=1, **kwargs)

    loop = ClosedLoop(
        policy=policy,
        client=client,
        registry=REGISTRY,
        observe=observe,
        episode_id="ep-test",
        max_snapshot_age_s=60.0,
        now=clock,
        max_observe_wait_s=0.2,
        observe_interval_s=0.01,
        condition=condition,
        platform="l1",
    )
    return loop, client, backend, body


class _RecordingPolicy:
    """Records the view it was given, then acts."""

    name = "recording"

    def __init__(self) -> None:
        self.views: list[dict] = []

    def reset(self) -> None:
        self.views = []

    def decide(self, view):
        self.views.append(view)
        return Finish(claimed_success=False, reason="done")

    def consume(self, receipt) -> None:
        pass

    def tokens_used(self) -> int:
        return 0


# ---------------------------------------------------------------- the switches


def test_no_receipts_hides_both_the_receipt_and_the_environments_own_report() -> None:
    """On L1 the observation carries `last_result`; hiding only the injected
    receipt would leave the answer visible."""
    policy = _RecordingPolicy()
    loop, _client, _backend, body = make_loop(policy, condition=LOOP_NO_RECEIPTS)
    body["last_result"] = {"status": "failed", "error_code": "TIMEOUT"}

    loop.run()
    payload = policy.views[0].get("payload", {})
    assert "last_result" not in payload
    assert "receipt" not in payload


def test_receipts_visible_puts_the_outcome_in_the_view() -> None:
    policy = _RecordingPolicy()
    loop, _client, _backend, body = make_loop(policy, condition=LOOP_RECEIPTS)
    body["last_result"] = {"status": "failed", "error_code": "TIMEOUT"}

    loop.run()
    # The first view is before any command, so there is no receipt yet.
    assert "receipt" not in policy.views[0].get("payload", {})


def test_the_receipt_appears_after_the_first_command() -> None:
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(policy, condition=LOOP_RECEIPTS)

    def decide(view):
        policy.views.append(view)
        if len(policy.views) == 1:
            return Decide(capability=ARM, arguments=ARM_ARGS)
        return Finish(claimed_success=False, reason="done")

    policy.decide = decide  # type: ignore[method-assign]
    loop.run()

    assert len(policy.views) >= 2
    receipt = policy.views[-1]["payload"].get("receipt")
    assert receipt is not None
    assert receipt["execution_status"] in {s.value for s in ExecutionStatus}
    assert "failure_code" in receipt
    assert "physical_effect_verified" in receipt


def test_physical_effect_verified_none_and_false_are_both_visible() -> None:
    """`None` is "nobody checked" and `False` is "checked, and it did not
    happen". A policy that cannot tell them apart treats a refusal as success."""
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(policy, condition=LOOP_RECEIPTS)

    def decide(view):
        policy.views.append(view)
        if len(policy.views) == 1:
            return Decide(capability=ARM, arguments=ARM_ARGS)
        return Finish(claimed_success=False, reason="done")

    policy.decide = decide  # type: ignore[method-assign]
    loop.run()
    receipt = policy.views[-1]["payload"]["receipt"]
    assert "physical_effect_verified" in receipt


def test_recovery_adds_the_registry_s_operations() -> None:
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(policy, condition=LOOP_RECEIPTS_RECOVERY)
    loop.run()
    capabilities = policy.views[0].get("capabilities")
    assert capabilities
    joined = " ".join(capabilities)
    assert "navigation.navigate_to_station" in joined


def test_recovery_is_absent_without_the_condition() -> None:
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(policy, condition=LOOP_RECEIPTS)
    loop.run()
    assert "capabilities" not in policy.views[0]


def test_the_recovery_list_hides_capabilities_the_model_may_not_call() -> None:
    """`describe_for` is the model-facing list; the grasp action is dispatchable
    but not offered, so it must not appear."""
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(policy, condition=LOOP_RECEIPTS_RECOVERY)
    loop.run()
    joined = " ".join(policy.views[0]["capabilities"])
    assert "manipulation.pick_and_place" not in joined


def test_the_freshness_gate_is_off_when_the_condition_says_so() -> None:
    """With the gate off, a snapshot that has gone stale is still usable."""
    clock = Clock()
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(
        policy, condition=LOOP_NO_RECEIPTS, clock=clock, taken_at=1000.0
    )
    # The snapshot was taken at 1000 and it is now 1500; the gate allows 60s.
    clock.t = 1500.0
    loop.run()
    assert policy.views, "a stale snapshot should still have been used"


def test_the_freshness_gate_refuses_a_stale_snapshot_when_it_is_on() -> None:
    clock = Clock()
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(
        policy, condition=LOOP_RECEIPTS_FRESH, clock=clock, taken_at=1000.0
    )
    clock.t = 1500.0
    loop.run()
    assert not policy.views, "a stale snapshot should have been refused"


def test_a_fresh_snapshot_is_accepted_by_the_gate() -> None:
    clock = Clock()
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(
        policy, condition=LOOP_RECEIPTS_FRESH, clock=clock, taken_at=1000.0
    )
    clock.t = 1010.0
    loop.run()
    assert policy.views


def test_a_frozen_clock_does_not_hang_the_wait() -> None:
    """A stopped simulation clock must not turn "no observation" into a hang.

    With `use_sim_time` the clock stops when Gazebo is paused. A deadline
    measured against that clock is never reached, so the loop would spin
    forever instead of reporting that there is nothing to see.
    """
    frozen = Clock(step=0.0)
    policy = _RecordingPolicy()
    loop, _client, _backend, _body = make_loop(
        policy, condition=LOOP_RECEIPTS_FRESH, clock=frozen, taken_at=1000.0
    )
    frozen.t = 1500.0  # the snapshot is stale and the clock will never move
    result = loop.run()
    assert result.failure == "no_observation"
    assert result.turns == 0


# ------------------------------------------------------------------ the set


def test_the_conditions_are_cumulative() -> None:
    """Each row switches on one more thing, so the table isolates one thing."""
    assert not LOOP_NO_RECEIPTS.receipts_visible
    assert not LOOP_NO_RECEIPTS.freshness_gate

    assert LOOP_RECEIPTS.receipts_visible
    assert not LOOP_RECEIPTS.freshness_gate

    assert LOOP_RECEIPTS_FRESH.receipts_visible
    assert LOOP_RECEIPTS_FRESH.freshness_gate
    assert not LOOP_RECEIPTS_FRESH.recovery_visible

    assert LOOP_RECEIPTS_RECOVERY.receipts_visible
    assert LOOP_RECEIPTS_RECOVERY.freshness_gate
    assert LOOP_RECEIPTS_RECOVERY.recovery_visible


def test_the_default_is_unchanged_behaviour() -> None:
    """The loop behaved this way before the conditions existed."""
    assert DEFAULT_CONDITION is LOOP_RECEIPTS_FRESH
    assert DEFAULT_CONDITION.receipts_visible
    assert DEFAULT_CONDITION.freshness_gate
    assert not DEFAULT_CONDITION.recovery_visible


def test_a_condition_can_be_named() -> None:
    assert by_name("loop_no_receipts") is LOOP_NO_RECEIPTS
    with pytest.raises(KeyError):
        by_name("no_such_condition")


def test_every_condition_says_what_it_isolates() -> None:
    for condition in LOOP_CONDITIONS:
        assert condition.isolates
        assert condition.name in condition.describe()


def test_the_conditions_are_immutable() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        LOOP_RECEIPTS.receipts_visible = False  # type: ignore[misc]
