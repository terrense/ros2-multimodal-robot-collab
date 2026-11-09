# Phase 3 Tasks

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## T0 — Approval gate

- [x] Write `docs/specs/phase3/spec.md`
- [x] User approved the spec and the five recommendations
- [x] Implementation started only after approval

## T1 — Contract v1.1 (additive)

- [x] `ExecutionReceipt.backend_ref: str | None`
- [x] `FailureCode`: `duplicate_command`, `stale_state`, `not_reconciled`, `stop_timeout`
- [x] Backward compatibility test: a v1 receipt without `backend_ref` still parses
- [x] Schema documents updated; existing tests stay green

## T2 — Ledger

- [x] `agent_harness/runtime/ledger.py` on stdlib `sqlite3`
- [x] Schema exactly as in the spec; one transaction per event
- [x] `record_submitted`, `record_started`, `record_finished`,
      `record_event`, `unresolved`, `mark_orphans`, `acknowledge_unknown`
- [x] Duplicate `command_id` raises rather than updating
- [x] Reopen test: the row survives a process boundary

## T3 — Reconciliation

- [x] `unknown` only leaves with evidence: a late receipt is applied by
      `record_finished`, an orphan is only cleared by `acknowledge_unknown`
- [x] Handle-alive path: the real receipt arriving later is written once and
      terminal rows are never rewritten
- [x] Restart marks orphans; side-effecting submits refused while unresolved
- [x] Operator acknowledgement CLI (`--note` required, recorded as `operator:<name>`)

## T4 — ROS 2 adapter

- [~] **Deviated** — see the note below
- [x] Capture goal uuid after acceptance (`ros_tool_executor._execute_action`)
      — implemented, **NOT RUN**
- [x] Correlation written before the result waits: ledger row exists from
      submission, `goal_uuid` backfilled from the receipt
- [x] Feedback flows into `receipt.result`, never changes terminal state
- [x] Stale `state_version` gate (client level; no adapter state source yet)
- [x] Bounded waits (wait_for_server and result waits take the command timeout)

### Deviation note (T4)

The approved spec asked for a `MultiThreadedExecutor` and per-resource callback
groups in this phase. It is deferred to Phase 3.1, for a concrete reason:
`RosToolExecutor` owns its own `spin_once` loop inside every dispatch method, so
adding a `MultiThreadedExecutor` without restructuring the executor would mean
two things spinning one node. That restructuring is exactly the kind of change
that must not be made without a live ROS 2 environment to test it in — and this
session has none.

What that means in practice:

- cross-resource concurrency remains proved by the fake backend only;
- the ROS backend serialises dispatch through one node (documented in
  `tool-runtime.md`);
- correlation, ledger, reconciliation and control semantics — the safety core —
  are complete and tested without ROS.

## T5 — Control paths

- [x] `cancel` (action cancel, bounded)
- [x] `pause` (arm control command, `source=operator_control`)
- [x] `stop` (topic command + fail-closed flag), labelled not-an-E-stop in the
      CLI help and in `control.py`
- [x] CLI: `agent_harness/scripts/robot_control.py` with `pause`/`stop`/
      `resume`/`ack`; `ack` needs no ROS and is tested end to end

## T6 — Fault injection

- [x] The eight faults from the spec matrix, against the fake environment
- [x] `docs/openrua_audit/fault-injection.md` with the result of each
- [x] State machine verified against the tests

## T7 — Verification and hand-off

- [x] Dry-run golden + 19 harness tests unchanged
- [x] `ruff` clean, full pytest green: **244 passed, 3 skipped**
- [x] `--engine legacy` untouched and still reachable
- [x] ROS 2 build status recorded; hardware **NOT RUN**
- [ ] Report before Phase 4

## Gate

- [x] No change to any ROS 2 node's control logic
      (only `_execute_action` gained optional timeout and goal-uuid capture)
- [x] No new runtime dependency (sqlite3 is stdlib)
- [x] No claim of `physical_effect_verified=True` without observation
