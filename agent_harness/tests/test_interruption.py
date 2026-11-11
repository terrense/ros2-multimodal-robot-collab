"""The model can die while the robot is moving. (Phase 5, P5-R8)

The master plan names this case explicitly: "model interrupted while physical
job runs". It was the last acceptance requirement in Phase 5 without a test,
because there was no such thing as an interruption to test.

Three things have to be true, and the third is the one that matters:

1. the interruption ends the episode;
2. **the physical work is not touched** -- an interruption is not a cancellation,
   and the loop has no authority to stop a base that is already driving to a
   station;
3. the episode's success is **unknown**, not false. The episode never finished,
   and a command that was in flight may still complete. Reporting it as a
   failure would put "the model died" and "the task failed" in the same bucket,
   which is exactly the kind of merging that makes a report untrustworthy.

One note on the fixture: a gated backend leaves a scheduler worker blocked
inside the backend, and the interpreter's exit hook joins those workers. A test
that leaves one blocked hangs the whole run *after* every test has passed, so
the fixture releases the gate and drains the client.
"""

from __future__ import annotations

import threading

import pytest

from agent_harness.agent.loop import ClosedLoop
from agent_harness.agent.policies import ScriptedPolicy
from agent_harness.agent.policy import Decide, Interrupted
from agent_harness.contracts import CapabilityRegistry
from agent_harness.runtime.client import ToolClient
from agent_harness.runtime.fake_backend import FakeBackend
from agent_harness.runtime.snapshots import SnapshotBuilder

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)

ARM = "manipulation.publish_arm_control"
ARM_ARGS = {"command": "arm_start"}


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        value = self.t
        self.t += 0.01
        return value


class _DyingPolicy:
    """Decides once, then dies."""

    name = "dying"

    def __init__(self, die_after: int = 1) -> None:
        self.calls = 0
        self.die_after = die_after

    def reset(self) -> None:
        self.calls = 0

    def decide(self, view):
        self.calls += 1
        if self.calls > self.die_after:
            raise Interrupted("the model backend died")
        return Decide(capability=ARM, arguments=ARM_ARGS)

    def consume(self, receipt) -> None:
        pass

    def tokens_used(self) -> int:
        return 0


class _BlockingPolicy:
    """Blocks inside `decide` until it is released.

    The block makes the timing deterministic: an interruption from another
    thread has to land while the loop is inside a decision, not after the
    episode has already finished.
    """

    name = "blocking"

    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()

    def reset(self) -> None:
        self.reached.clear()
        self.release.clear()

    def decide(self, view):
        self.reached.set()
        self.release.wait(5.0)
        return Decide(capability=ARM, arguments=ARM_ARGS)

    def consume(self, receipt) -> None:
        pass

    def tokens_used(self) -> int:
        return 0


@pytest.fixture
def make_loop():
    """Build a loop, and make sure nothing is left running afterwards."""
    created: list[tuple[ToolClient, FakeBackend]] = []

    def factory(policy, *, gate: bool = False):
        clock = _Clock()
        backend = FakeBackend()
        if gate:
            backend.gate = threading.Event()
        client = ToolClient(backend, REGISTRY)
        created.append((client, backend))
        builder = SnapshotBuilder(
            sensor_id="d435",
            calibration_version="calib-test",
            frame_id="map",
            clock=clock,
        )

        def observe():
            return builder.build({"pose": [0.0, 0.0]}, state_version=1)

        loop = ClosedLoop(
            policy=policy,
            client=client,
            registry=REGISTRY,
            observe=observe,
            episode_id="ep-interrupt",
            max_snapshot_age_s=60.0,
            now=clock,
            max_observe_wait_s=0.2,
            observe_interval_s=0.01,
        )
        return loop, client, backend, clock

    yield factory

    for client, backend in created:
        if backend.gate is not None:
            backend.gate.set()
        client.shutdown(5.0)


def test_an_interrupted_model_ends_the_episode(make_loop) -> None:
    policy = _DyingPolicy(die_after=1)
    loop, _client, _backend, _clock = make_loop(policy)
    result = loop.run()
    assert result.failure == "interrupted"
    assert result.outcome.failure_category == "interrupted"


def test_the_success_of_an_interrupted_episode_is_unknown_not_false(make_loop) -> None:
    """The episode never finished, so its outcome is unknown.

    `False` would say the task failed. It did not fail; it was never allowed to
    finish. And the command that was in flight may still complete.
    """
    policy = _DyingPolicy(die_after=1)
    loop, _client, _backend, _clock = make_loop(policy)
    result = loop.run()
    assert result.outcome.success is None
    assert result.outcome.success is not False


def test_an_interruption_leaves_the_physical_work_alone(make_loop) -> None:
    """The base that was already driving keeps driving.

    An interruption is not a cancellation: nobody asked the arm to stop, and a
    model that died is not in a position to authorise a stop.
    """
    policy = _DyingPolicy(die_after=1)
    loop, client, backend, _clock = make_loop(policy, gate=True)
    holder: list = []

    worker = threading.Thread(target=lambda: holder.append(loop.run()), daemon=True)
    worker.start()
    assert backend.gate_entered.wait(2.0), "the command never reached the backend"

    # The model dies on its second decision, while the first command runs.
    policy.die_after = 0
    backend.gate.set()
    worker.join(5.0)

    assert worker.is_alive() is False
    command_ids = [call.command_id for call in backend.calls]
    assert command_ids, "the command was never dispatched"
    status = client.job_status(command_ids[0])
    assert status not in ("cancelled",), (
        f"an interruption cancelled physical work (status={status!r})"
    )


def test_the_report_says_how_much_work_may_still_be_running(make_loop) -> None:
    """"The model died" and "the model died and the arm is still moving" are
    different findings.

    The interruption has to arrive while a command is in flight, which is the
    case the wait had to become interruptible for: a command that takes thirty
    seconds used to keep the episode alive for thirty seconds after the
    decision source died.
    """
    policy = ScriptedPolicy([Decide(ARM, ARM_ARGS)])
    loop, _client, backend, _clock = make_loop(policy, gate=True)
    holder: list = []

    worker = threading.Thread(target=lambda: holder.append(loop.run()), daemon=True)
    worker.start()
    assert backend.gate_entered.wait(2.0), "the command never reached the backend"

    # The command is in flight and blocked; the decision source is interrupted.
    loop.interrupt("watchdog fired")
    worker.join(5.0)

    result = holder[0]
    assert result.failure == "interrupted"
    assert "interrupted" in result.outcome.detail
    assert "still be running" in result.outcome.detail
    assert result.outcome.success is None


def test_an_interruption_from_outside_is_seen_at_the_next_decision(make_loop) -> None:
    """A signal handler or a watchdog interrupts the loop from another thread."""
    policy = _BlockingPolicy()
    loop, _client, _backend, _clock = make_loop(policy)
    holder: list = []

    worker = threading.Thread(target=lambda: holder.append(loop.run()), daemon=True)
    worker.start()
    assert policy.reached.wait(2.0), "the policy never reached a decision"

    loop.interrupt("watchdog fired")
    policy.release.set()
    worker.join(5.0)

    result = holder[0]
    assert result.failure == "interrupted"
    assert result.outcome.success is None
    assert "watchdog fired" in result.outcome.detail


def test_an_interrupted_decision_is_not_dispatched(make_loop) -> None:
    """Nobody is left to authorise the action, so it does not reach the robot."""
    policy = _BlockingPolicy()
    loop, _client, backend, _clock = make_loop(policy)
    holder: list = []

    worker = threading.Thread(target=lambda: holder.append(loop.run()), daemon=True)
    worker.start()
    assert policy.reached.wait(2.0), "the policy never reached a decision"

    loop.interrupt("watchdog fired")
    policy.release.set()
    worker.join(5.0)

    assert holder[0].failure == "interrupted"
    assert not backend.calls, "an action was dispatched after the interruption"


def test_a_cancellation_is_still_a_cancellation(make_loop) -> None:
    """The two must not collapse into each other."""
    policy = _BlockingPolicy()
    loop, _client, _backend, _clock = make_loop(policy)
    holder: list = []

    worker = threading.Thread(target=lambda: holder.append(loop.run()), daemon=True)
    worker.start()
    assert policy.reached.wait(2.0), "the policy never reached a decision"

    loop.cancel()
    policy.release.set()
    worker.join(5.0)

    result = holder[0]
    assert result.failure == "cancelled"
    assert result.outcome.success is False


def test_a_normal_episode_is_not_an_interruption(make_loop) -> None:
    policy = ScriptedPolicy([Decide(ARM, ARM_ARGS)])
    loop, _client, _backend, _clock = make_loop(policy)
    result = loop.run()
    assert result.failure is None
    assert result.outcome.failure_category != "interrupted"
