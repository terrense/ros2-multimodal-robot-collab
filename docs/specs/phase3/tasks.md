# Phase 3 Tasks

> **OpenRUA assimilation — Phase O3.** Not to be confused with the post-training
> line (`PT3` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R3` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

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
      — implemented and **RUN** against the simulated stack; see
      `docs/openrua_audit/ros2-validation.md`
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
- [x] Report before Phase 4

## Gate

- [x] No change to any ROS 2 node's control logic
      (only `_execute_action` gained optional timeout and goal-uuid capture)
- [x] No new runtime dependency (sqlite3 is stdlib)
- [x] No claim of `physical_effect_verified=True` without observation

## Phase 3.1 — ROS validation (branch `phase3.1/ros-validation`)

- [x] Build the workspace on WSL Ubuntu 22.04 + ROS 2 Humble:
      `Summary: 10 packages finished [2min 26s]`, exit 0
- [x] Launch the simulated stack and query state:
      `state=IDLE`, all seven nodes active, `motion_enabled=True`
- [x] End-to-end delivery plan through `--engine runtime`: both steps
      `succeeded`, `harness_exit=0`
- [x] Wire the ledger into the execute CLI (`--ledger`); orphans are marked at
      startup and side effects blocked until acknowledged
- [x] Verify command ↔ goal uuid correlation on real goals:
      `e86c3cdbb3854988b1cedffa2c1075f5`, `1714300f840748358266073ecb6bd079`
- [x] Reproducible script: `scripts/smoke_ros2_runtime.sh`
- [ ] MultiThreadedExecutor + per-resource callback groups — still outstanding
- [ ] Fault injection on the wire (DDS-level drops) — still outstanding

## What the two open items need

**Concurrency.** The scheduler already runs different resources at the same
time, and the fake backend proves it. What is missing is the ROS 2 side:
`RosToolExecutor` spins one node from one thread and `RosBackend` holds a
global lock, so every dispatch serialises. The fix is a `MultiThreadedExecutor`
with one callback group per resource key, so the arm and the base can move at
once while two calls on the same resource still queue.

**Wire faults.** The eight transport faults are injected in-process, which
proves the *state machine* is right but not that the real transport is. What is
missing is packet loss and delay injected below the application, with the
stack running, to see whether a live system still refuses to double-apply a
command whose effect is uncertain.
