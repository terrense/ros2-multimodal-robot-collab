"""The ROS-side concurrency the scheduler was promised (O3.1).

The scheduler has always grouped work by resource and run different resources
at the same time; the fake backend proves it. The ROS 2 side did not follow:
one node spun from one thread and one process-wide lock meant every dispatch
serialised, whichever resource it belonged to.

Both halves of the fix are pure Python, so they are tested here without ROS 2:

    ResourceLocks   one lock per resource, not one for the process
    wait_for_future wait on a signal instead of pumping spin_once

What is NOT RUN: the MultiThreadedExecutor path itself. It needs a live node
and a live action server, and there is neither on this machine. The tests below
pin the logic around it, not the ROS 2 behaviour.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from agent_harness.runtime.concurrency import (
    ConcurrentWait,
    ResourceLocks,
    SerialWait,
    wait_for_future,
)
from agent_harness.runtime.ros_backend import RosBackend

# --------------------------------------------------------------- the locks


def test_two_resources_do_not_block_each_other() -> None:
    """The whole point: an arm command must not wait for a base command."""
    locks = ResourceLocks()
    order: list[str] = []
    holding = threading.Event()
    release = threading.Event()

    def hold_arm() -> None:
        with locks.hold("arm"):
            order.append("arm acquired")
            holding.set()
            release.wait(2.0)
            order.append("arm released")

    worker = threading.Thread(target=hold_arm, daemon=True)
    worker.start()
    assert holding.wait(2.0)

    # The arm lock is held; the base must still be obtainable.
    assert locks.held("arm")
    with locks.hold("base"):
        order.append("base acquired")
    assert not locks.held("base")

    release.set()
    worker.join(2.0)
    assert order == ["arm acquired", "base acquired", "arm released"]


def test_one_resource_still_serialises_with_itself() -> None:
    """Different resources run together; the same resource is exclusive."""
    locks = ResourceLocks()
    inside: list[int] = []
    peak = [0]
    lock_guard = threading.Lock()
    release = threading.Event()
    first_inside = threading.Event()

    def worker(_index: int) -> None:
        with locks.hold("arm"):
            with lock_guard:
                inside.append(1)
                peak[0] = max(peak[0], len(inside))
            first_inside.set()
            release.wait(2.0)
            with lock_guard:
                inside.pop()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(3)]
    for thread in threads:
        thread.start()
    assert first_inside.wait(2.0)
    time.sleep(0.05)  # give the other two a chance to (incorrectly) enter
    assert peak[0] == 1
    release.set()
    for thread in threads:
        thread.join(2.0)


def test_locks_are_created_on_first_use_and_reused() -> None:
    locks = ResourceLocks()
    assert locks.keys() == []
    first = locks.for_key("arm")
    second = locks.for_key("arm")
    assert first is second
    assert locks.keys() == ["arm"]


# ----------------------------------------------------------- waiting


@dataclass
class _Future:
    """Enough of an rclpy future for the wait logic."""

    _done: bool = False

    def done(self) -> bool:
        return self._done

    def finish(self) -> None:
        self._done = True


def test_waiting_for_an_already_done_future_returns_immediately() -> None:
    future = _Future(_done=True)
    assert wait_for_future(future, timeout_sec=0.0)


def test_a_future_that_finishes_during_the_wait_is_seen() -> None:
    future = _Future()
    threading.Timer(0.05, future.finish).start()
    assert wait_for_future(future, timeout_sec=2.0)


def test_a_future_that_never_finishes_times_out() -> None:
    future = _Future()
    started = time.monotonic()
    assert not wait_for_future(future, timeout_sec=0.1)
    assert time.monotonic() - started < 2.0


def test_concurrent_wait_does_not_spin_the_node() -> None:
    """In concurrent mode the caller must not be a second spinner."""
    future = _Future(_done=True)
    wait = ConcurrentWait()
    assert wait.concurrent
    assert wait.wait(future, 0.1)


def test_serial_wait_keeps_driving_the_node() -> None:
    """The pre-O3.1 behaviour stays available and unchanged."""
    spins: list[float] = []

    def spin_once(timeout: float) -> None:
        spins.append(timeout)

    future = _Future(_done=True)
    wait = SerialWait(spin_once)
    assert not wait.concurrent
    assert wait.wait(future, 0.1)
    assert spins == []  # already done: nothing to pump


# ------------------------------------------------------- the backend lock


class _RecordingExecutor:
    """Records which resource keys were dispatching at the same time."""

    max_workers = 1

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.active: list[str] = []
        self.peak = 0
        self._guard = threading.Lock()

    def _record(self, resource_key: str | None) -> None:
        with self._guard:
            self.active.append(resource_key or "episode")
            self.peak = max(self.peak, len(self.active))

    def _forget(self, resource_key: str | None) -> None:
        with self._guard:
            self.active.remove(resource_key or "episode")

    def _execute_action(self, record, timeout_s=None, resource_key=None) -> None:
        self._record(resource_key)
        time.sleep(self.delay)
        record.status = "succeeded"
        self._forget(resource_key)

    def _execute_service(self, record, timeout_s=None, resource_key=None) -> None:
        self._execute_action(record, timeout_s, resource_key)

    def _execute_topic(self, record) -> None:
        record.status = "succeeded"


class _Registry:
    """A registry that knows two action capabilities."""

    def get_action(self, skill: str, action: str) -> dict:
        return {
            "ros_action": f"/skills/{skill}_{action}",
            "ros_type": "pkg/action/Demo",
        }


def test_the_backend_lets_different_resources_overlap() -> None:
    executor = _RecordingExecutor()
    backend = RosBackend(executor, _Registry())

    from agent_harness.contracts import ActionRequest

    def request(resource_key: str) -> ActionRequest:
        return ActionRequest(
            episode_id="ep-1",
            command_id=f"cmd-{resource_key}",
            capability="navigation.navigate_to_station",
            arguments={"station_id": "tool_shelf", "reason": "go"},
            resource_key=resource_key,
        )

    threads = [
        threading.Thread(target=backend.execute, args=(request(key),), daemon=True)
        for key in ("arm", "base")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)

    assert executor.peak == 2, "the base and the arm must be able to overlap"


def test_the_backend_keeps_one_resource_exclusive() -> None:
    executor = _RecordingExecutor()
    backend = RosBackend(executor, _Registry())

    from agent_harness.contracts import ActionRequest

    def request(index: int) -> ActionRequest:
        return ActionRequest(
            episode_id="ep-1",
            command_id=f"cmd-arm-{index}",
            capability="manipulation.publish_arm_control",
            arguments={
                "command": "arm_start",
                "operator_id": "operator_001",
                "source": "test",
                "confidence": 1.0,
                "emergency": False,
                "detail": "",
            },
            resource_key="arm",
        )

    threads = [
        threading.Thread(target=backend.execute, args=(request(i),), daemon=True)
        for i in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)

    assert executor.peak == 1, "two commands on one arm must not overlap"
