"""Concurrency for the ROS 2 side of the runtime (O3.1).

The scheduler already runs different resources at the same time, and the fake
backend proves it. What was missing is the ROS 2 side: `RosToolExecutor` spun
one node from one thread and `RosBackend` held a single global lock, so every
dispatch serialised no matter what the scheduler allowed.

Two pieces fix that, and both are pure Python so they can be tested on a
machine with no ROS 2:

* `ResourceLocks` — one lock per resource key instead of one lock for the
  process. The arm and the base are different physical resources; a command on
  one must not wait for a command on the other. Two commands on the *same*
  resource still serialise, because they are competing for the same hardware.
* `wait_for_future` — wait on a completed-signal instead of pumping
  `spin_once`. With a `MultiThreadedExecutor` running in a background thread,
  spinning from the waiting thread would be a second spinner on one node,
  which is exactly the mistake that makes callback ordering unpredictable.

The mapping from a capability to its resource key is not re-derived here: the
`ToolClient` has already normalised it from the capability descriptor, and the
descriptor owns the resource (an arm command must not be able to declare itself
non-exclusive). These helpers only consume the key they are given.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any, Iterator


class ResourceLocks:
    """One lock per resource key, created on first use."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def for_key(self, resource_key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(resource_key)
            if lock is None:
                lock = threading.Lock()
                self._locks[resource_key] = lock
            return lock

    @contextmanager
    def hold(self, resource_key: str) -> Iterator[None]:
        lock = self.for_key(resource_key)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def held(self, resource_key: str) -> bool:
        """Whether some thread currently holds this resource's lock."""
        return self.for_key(resource_key).locked()

    def keys(self) -> list[str]:
        with self._guard:
            return sorted(self._locks)


def wait_for_future(
    future: Any,
    timeout_sec: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    interval_s: float = 0.01,
) -> bool:
    """Wait for an rclpy future to finish without spinning the node.

    Returns False on timeout. A non-positive timeout means "wait forever",
    matching the executor's existing convention.

    The wait is a poll on `future.done()` rather than `future.add_done_callback`
    because rclpy resolves the callback in whichever executor thread is
    spinning the node; the callback would race with the caller's own timeout
    and could fire after the caller has already given up.
    """
    if future.done():
        return True
    started = clock()
    while not future.done():
        if timeout_sec > 0.0 and clock() - started > timeout_sec:
            return False
        time.sleep(interval_s)
    return True


class SerialWait:
    """The pre-O3.1 behaviour, kept as the default so nothing changes silently."""
    """The pre-O3.1 behaviour, kept as the default so nothing changes silently."""

    concurrent = False

    def __init__(self, spin_once: Callable[[float], None]) -> None:
        self._spin_once = spin_once

    def wait(self, future: Any, timeout_sec: float) -> bool:
        started = time.monotonic()
        while not future.done():
            self._spin_once(0.1)
            if timeout_sec > 0.0 and time.monotonic() - started > timeout_sec:
                return False
        return True


class ConcurrentWait:
    """Wait without spinning: a background executor is already spinning."""

    concurrent = True

    def wait(self, future: Any, timeout_sec: float) -> bool:
        return wait_for_future(future, timeout_sec)


__all__ = [
    "ConcurrentWait",
    "ResourceLocks",
    "SerialWait",
    "wait_for_future",
]
