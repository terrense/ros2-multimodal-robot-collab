# Phase 3 — Fault Injection Report

Scope: the execution ledger, reconciliation policy and control paths, exercised
against an in-process fake backend and a real SQLite database. **No ROS 2
process, Gazebo instance or robot was involved.** These results validate logic,
not physical behaviour.

Run: `pytest tests/test_execution_ledger.py` — 15 tests, all passing, at the
commit that adds this file.

| # | Fault | How it is injected | Expected | Test |
|---|---|---|---|---|
| 1 | ACK dropped after motion completed | backend returns `unknown` after a scripted timeout | row stays `unknown`; ledger keeps the command; no replay | `test_dropped_result_stays_unknown_and_blocks_side_effects` |
| 1b | write-before-dispatch | backend reads the ledger at execute time | row exists and is `running` before the backend runs | `test_submission_is_durable_before_dispatch` |
| 2 | Stale state version | client observes version 7, request carries 5 | refused `stale_state`, backend never called | `test_stale_state_is_refused_before_dispatch` |
| 3 | Duplicate command id | same request submitted twice | second refused `duplicate_command`; one execution; one row | `test_duplicate_command_executes_once` |
| 4 | Result dropped, process alive | scripted `unknown` receipt | `unknown` recorded; side effects blocked until resolved | `test_dropped_result_stays_unknown_and_blocks_side_effects` |
| 5 | Server restart | close ledger, reopen, `mark_orphans()` | orphan detected; side-effecting submits refused `not_reconciled`; operator acknowledgement clears it as `failed`, never `completed` | `test_restart_marks_orphans_and_blocks_until_acknowledged` |
| 6 | Cancellation race | cancel after the command completed | `cancel` returns False; row stays `completed`; no `cancelled` rewrite | `test_cancel_after_completion_does_not_rewrite_history` |
| 7 | Delayed feedback / late receipt | terminal receipt followed by a contradictory one | the second is ignored, an `ignored_terminal` event is appended | `test_delayed_receipt_after_terminal_is_recorded_not_applied` |
| 8 | Controller unavailable | backend reports `transport_error` failure | `failed`, not `unknown` — nothing was sent | `test_controller_failure_is_failed_not_unknown` |

Additional semantics verified:

| Behaviour | Test |
|---|---|
| Fail-closed blocks side effects, allows read-only, clears only with operator + note | `test_fail_closed_blocks_side_effects_and_needs_an_operator_to_clear` |
| Stop records `emergency=true`, raises fail-closed, resume requires a note | `test_stop_fails_closed_and_only_an_operator_can_resume` |
| Pause is an operator-issued arm command with `source=operator_control` | `test_pause_is_an_operator_arm_command` |
| Ledger survives a close/reopen | `test_ledger_survives_reopen` |
| Cross-resource commands are recorded independently | `test_concurrent_commands_in_different_resources_are_recorded_independently` |

## What these tests do not prove

1. **They do not prove the ROS 2 side.** The action servers, timeouts and
   cancellation behaviour are simulated by the fake backend. A real goal that
   is dropped by DDS behaves similarly in logic, not necessarily in timing.
2. **They do not prove durability under power loss.** SQLite in WAL mode is
   crash-safe for process death; a filesystem that lies about fsync is out of
   scope.
3. **They do not prove the multi-threaded adapter.** Deferred — see the
   deviation note in `docs/specs/phase3/tasks.md`.

Every statement above is about the code paths that ran in this repository on
this machine. Nothing here is a hardware validation.
