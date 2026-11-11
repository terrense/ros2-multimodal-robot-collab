"""Per-resource FIFO scheduling with queued-only cancellation.

Ownership: this scheduler orders *capabilities*. It never slices a running
capability into steps — the ROS 2 action server owns everything inside one goal.
That boundary is why there is exactly one worker per resource instead of a
pipeline of stages.

Responsibilities:

- one FIFO queue per resource key (``arm``, ``base``, ``episode``);
- at most one running job per resource at a time;
- different resources run concurrently;
- ``cancel`` only affects jobs that have not started;
- ``drain`` stops accepting work, cancels what is still queued, waits for what
  is running, and returns the ids that outlived the timeout.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from agent_harness.contracts import ExecutionReceipt

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
CANCELLED = "cancelled"


@dataclass
class Job:
    command_id: str
    resource_key: str
    fn: Callable[[], ExecutionReceipt]
    status: str = QUEUED
    receipt: ExecutionReceipt | None = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event, repr=False)


class SchedulerClosed(RuntimeError):
    """Raised when work is submitted after the scheduler was drained."""


class DuplicateCommand(RuntimeError):
    """Raised when a command id is submitted twice."""


class ResourceScheduler:
    def __init__(self, *, max_workers: int = 4) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._queues: dict[str, deque[Job]] = {}
        self._running: dict[str, Job] = {}
        self._jobs: dict[str, Job] = {}
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="tool-runtime"
        )
        self._closed = False
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="tool-dispatcher", daemon=True
        )
        self._dispatcher.start()

    # ------------------------------------------------------------------ submit

    def submit(self, job: Job) -> None:
        with self._condition:
            if self._closed:
                raise SchedulerClosed("scheduler is drained")
            if job.command_id in self._jobs:
                raise DuplicateCommand(f"command {job.command_id!r} was already submitted")
            self._jobs[job.command_id] = job
            self._queues.setdefault(job.resource_key, deque()).append(job)
            self._condition.notify_all()

    # --------------------------------------------------------------- dispatch

    def _dispatch_loop(self) -> None:
        while True:
            with self._condition:
                while not self._closed and not self._runnable():
                    self._condition.wait()
                if self._closed:
                    return
                job = self._pop_runnable()
                job.status = RUNNING
                self._running[job.resource_key] = job
            self._pool.submit(self._execute, job)

    def _runnable(self) -> bool:
        return any(
            queue and resource not in self._running
            for resource, queue in self._queues.items()
        )

    def _pop_runnable(self) -> Job:
        for resource, queue in self._queues.items():
            if queue and resource not in self._running:
                return queue.popleft()
        raise RuntimeError("dispatcher woke without a runnable job")

    def _execute(self, job: Job) -> None:
        receipt: ExecutionReceipt | None = None
        error: str | None = None
        try:
            receipt = job.fn()
        except BaseException as exc:  # noqa: BLE001 - one job failure must not kill the worker
            error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._condition:
                self._running.pop(job.resource_key, None)
                job.status = DONE
                job.receipt = receipt
                job.error = error
                job.done.set()
                self._condition.notify_all()

    # ---------------------------------------------------------------- control

    def cancel(self, command_id: str) -> bool:
        """Cancel a queued job. Running work is left alone, deliberately."""
        with self._condition:
            job = self._jobs.get(command_id)
            if job is None or job.status is not QUEUED:
                return False
            queue = self._queues[job.resource_key]
            queue.remove(job)
            job.status = CANCELLED
            job.done.set()
            self._condition.notify_all()
            return True

    def wait(self, command_id: str, timeout: float | None = None) -> Job | None:
        with self._condition:
            job = self._jobs.get(command_id)
        if job is None:
            return None
        job.done.wait(timeout)
        return job

    def job(self, command_id: str) -> Job | None:
        with self._condition:
            return self._jobs.get(command_id)

    def queue_depth(self, resource_key: str) -> int:
        with self._condition:
            return len(self._queues.get(resource_key, ()))

    def running_count(self) -> int:
        """How many commands are executing right now.

        The question matters when something interrupts the decision source: the
        physical work in flight keeps running, and the report has to say how
        much of it there was.
        """
        with self._condition:
            return len(self._running)

    def is_running(self, resource_key: str) -> bool:
        with self._condition:
            return resource_key in self._running

    # ------------------------------------------------------------------ drain

    def drain(self, timeout: float = 30.0) -> list[str]:
        """Stop accepting, cancel queued work, wait for running work.

        Returns the command ids that were still running when the timeout
        expired. They are not cancelled: the caller must report them.
        """
        with self._condition:
            if not self._closed:
                self._closed = True
                for queue in self._queues.values():
                    while queue:
                        job = queue.popleft()
                        job.status = CANCELLED
                        job.done.set()
            running = list(self._running.values())
            self._condition.notify_all()

        deadline = time.monotonic() + max(0.0, timeout)
        for job in running:
            job.done.wait(max(0.0, deadline - time.monotonic()))

        with self._condition:
            unfinished = [
                job.command_id
                for job in self._jobs.values()
                if job.status not in {DONE, CANCELLED}
            ]
            self._condition.notify_all()
        self._pool.shutdown(wait=False, cancel_futures=True)
        return unfinished
