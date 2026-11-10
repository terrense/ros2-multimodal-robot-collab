"""The closed loop: observe one snapshot, decide one action, read the receipt.

Phase 5 composes the earlier phases. The rules below are not stylistic; each
one closes a failure mode found in the audit:

- one action per decision (a plan is a macro, not a list of actions);
- ``unknown`` stops the episode and is never resubmitted;
- a policy's success claim never becomes the episode verdict;
- finishing with a non-terminal receipt is ``premature_finish``;
- ``cancel`` requests policy cancellation and leaves physical work alone.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from agent_harness.contracts import (
    ActionRequest,
    CapabilityRegistry,
    ExecutionReceipt,
    ExecutionStatus,
    ObservationSnapshot,
    StaleSnapshot,
    TaskOutcome,
    UnsupportedCapability,
    new_command_id,
)
from agent_harness.runtime.client import ToolClient
from agent_harness.runtime.snapshots import model_view

from .budgets import Budgets
from .policy import Decide, EpisodeResult, Finish, Policy, wall_clock

_TERMINAL = frozenset(
    {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.REJECTED}
)


class ClosedLoop:
    def __init__(
        self,
        *,
        policy: Policy,
        client: ToolClient,
        registry: CapabilityRegistry,
        observe: Callable[[], ObservationSnapshot | None],
        episode_id: str,
        max_snapshot_age_s: float = 1.0,
        budgets: Budgets | None = None,
        now: Callable[[], float] = wall_clock,
        outcome_source: Callable[[], TaskOutcome] | None = None,
        max_observe_wait_s: float = 2.0,
        observe_interval_s: float = 0.05,
    ) -> None:
        self._policy = policy
        self._client = client
        self._registry = registry
        self._observe = observe
        self._episode_id = episode_id
        self._max_age = max_snapshot_age_s
        self._budgets = budgets or Budgets()
        self._now = now
        self._outcome_source = outcome_source
        self._max_observe_wait = max_observe_wait_s
        self._observe_interval = observe_interval_s
        self._cancelled = threading.Event()
        self._cancel_reason: str | None = None

    # ------------------------------------------------------------------ run

    def run(self, task: str = "") -> EpisodeResult:
        del task  # the task text belongs to the prompt, not to execution
        self._policy.reset()
        self._cancelled.clear()
        self._cancel_reason = None
        started = self._now()
        receipts: list[ExecutionReceipt] = []
        turns = 0
        view = self._view()

        while True:
            if self._cancelled.is_set():
                return self._result(
                    TaskOutcome(success=False, accepted=True, failure_category="cancelled"),
                    receipts=receipts,
                    turns=turns,
                    started=started,
                    failure="cancelled",
                )

            reason = self._budgets.exceeded(
                turns=turns,
                tokens=self._policy.tokens_used(),
                elapsed_s=self._now() - started,
            )
            if reason is not None:
                return self._result(
                    TaskOutcome(success=None, accepted=True, failure_category=reason),
                    receipts=receipts,
                    turns=turns,
                    started=started,
                    failure=reason,
                )

            if view is None:
                view = self._await_view()
                if view is None:
                    return self._result(
                        TaskOutcome(success=None, accepted=True, failure_category="no_observation"),
                        receipts=receipts,
                        turns=turns,
                        started=started,
                        failure="no_observation",
                    )
                continue

            decision = self._policy.decide(view)
            if isinstance(decision, Finish):
                return self._finish(decision, receipts, turns, started)
            if not isinstance(decision, Decide):
                # P5-R1: one action per decision; a list is a policy bug.
                return self._result(
                    TaskOutcome(success=False, accepted=True, failure_category="invalid_decision"),
                    receipts=receipts,
                    turns=turns,
                    started=started,
                    failure="invalid_decision",
                )

            if self._cancelled.is_set():
                # Cancellation between deciding and submitting stops the action
                # before it reaches the robot.
                return self._result(
                    TaskOutcome(success=False, accepted=True, failure_category="cancelled"),
                    receipts=receipts,
                    turns=turns,
                    started=started,
                    failure="cancelled",
                )

            receipt = self._execute(decision)
            if receipt is None:
                return self._result(
                    TaskOutcome(success=False, accepted=True, failure_category="invalid_capability"),
                    receipts=receipts,
                    turns=turns,
                    started=started,
                    failure="invalid_capability",
                )

            receipts.append(receipt)
            turns += 1
            self._policy.consume(receipt.to_dict())

            if receipt.execution_status is ExecutionStatus.UNKNOWN:
                # P5-R2: an unknown physical effect is never resubmitted.
                return self._result(
                    TaskOutcome(success=None, accepted=True, failure_category="execution_uncertain"),
                    receipts=receipts,
                    turns=turns,
                    started=started,
                    failure="unknown_outcome",
                )
            view = self._view()

    # ------------------------------------------------------------- helpers

    def cancel(self, reason: str = "cancelled") -> None:
        """Request policy cancellation. Running commands are not touched."""
        self._cancel_reason = reason
        self._cancelled.set()

    def _execute(self, decision: Decide) -> ExecutionReceipt | None:
        try:
            self._registry.require(decision.capability, "ros2")
        except UnsupportedCapability:
            return None
        request = ActionRequest(
            episode_id=self._episode_id,
            command_id=new_command_id(),
            capability=decision.capability,
            arguments=dict(decision.arguments),
        )
        submitted = self._client.submit(request)
        if submitted.execution_status is ExecutionStatus.QUEUED:
            return self._client.wait(request.command_id)
        return submitted

    def _view(self) -> dict | None:
        snapshot = self._observe()
        if snapshot is None:
            return None
        try:
            return model_view(snapshot, now_s=self._now(), max_age_s=self._max_age)
        except StaleSnapshot:
            return None

    def _await_view(self) -> dict | None:
        deadline = self._now() + self._max_observe_wait
        while self._now() < deadline:
            time.sleep(self._observe_interval)
            view = self._view()
            if view is not None:
                return view
        return None

    def _finish(
        self,
        finish: Finish,
        receipts: list[ExecutionReceipt],
        turns: int,
        started: float,
    ) -> EpisodeResult:
        outcome = (
            self._outcome_source()
            if self._outcome_source is not None
            else TaskOutcome(success=None, accepted=True)
        )
        failure: str | None = None
        last = receipts[-1] if receipts else None

        if finish.claimed_success:
            if last is None or last.execution_status not in _TERMINAL:
                failure = "premature_finish"
                outcome = TaskOutcome(
                    success=False, accepted=True, failure_category=failure
                )
            elif outcome.success is not True:
                # The environment did not confirm; the claim is not a verdict.
                failure = "false_success"
                outcome = TaskOutcome(
                    success=False, accepted=True, failure_category=failure
                )

        result = self._result(
            outcome, receipts=receipts, turns=turns, started=started, failure=failure
        )
        result.finish = finish
        result.claimed_success = finish.claimed_success
        return result

    def _result(
        self,
        outcome: TaskOutcome,
        *,
        receipts: list[ExecutionReceipt],
        turns: int,
        started: float,
        failure: str | None,
    ) -> EpisodeResult:
        return EpisodeResult(
            outcome=outcome,
            turns=turns,
            receipts=[receipt.to_dict() for receipt in receipts],
            failure=failure,
            budgets={
                **self._budgets.as_dict(),
                "turns_used": turns,
                "tokens_used": self._policy.tokens_used(),
                "elapsed_s": round(self._now() - started, 3),
            },
        )
