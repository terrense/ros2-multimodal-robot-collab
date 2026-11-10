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
- [x] `--engine legacy` untouched and still reachable — *at the time*; the flag was removed in O3.1, see below
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
- [x] MultiThreadedExecutor + per-resource callback groups — `agent_harness/runtime/concurrency.py` (`ResourceLocks`, `wait_for_future`), `RosBackend` keyed by resource, `RosToolExecutor(concurrent=True)` with one `ReentrantCallbackGroup` per resource and `--concurrent` on the CLI. The logic is tested without ROS; the live MultiThreadedExecutor path is **NOT RUN**.
- [x] Fault injection on the wire (DDS-level drops) — `agent_harness/runtime/wire_faults.py` (netem rules + the three invariants) and `scripts/wire_fault_check.py` (apply, dispatch, restore in a `finally`, audit the ledger). **NOT RUN**: needs root, a live stack and a person.
- [x] `--engine legacy` removed — the pre-runtime loop dispatched straight to the executor: no ledger, no reconciliation, no resource scheduling. Keeping it as a retreat hatch meant keeping the one way to move the robot and leave no evidence. There is now one execute path, and `execute_plan` is gone rather than merely unreachable.

## What is done on concurrency, and what is not

**Done.** `RosBackend` no longer holds one process-wide lock: it takes a lock
per resource key, so an arm command and a base command no longer wait for each
other. `RosToolExecutor(concurrent=True)` starts a `MultiThreadedExecutor` in a
background thread, creates one `ReentrantCallbackGroup` per resource, and
`_spin_until_done` stops pumping `spin_once` — with a background executor,
spinning from the waiting thread would be two spinners on one node.
`agent_harness/tests/test_concurrency.py` proves the overlap and the exclusion
against a recording executor, with no ROS 2 installed.

**NOT RUN.** The live path. It needs a node and an action server to prove that
`ReentrantCallbackGroup` actually lets the base and the arm make progress
simultaneously through rclpy, and that `wait_for_future` never misses a
completion that the executor thread resolves. Nothing on this machine can
answer that; the code is in place and the default is still the serial path.

## Wire faults: what is built, and what it will prove

The eight transport faults the runtime handles are injected in-process against
a fake backend. That proves the *state machine*; it does not prove the
transport, because a real link can lose a message after the robot moved and
before anyone heard about it.

`scripts/wire_fault_check.py` closes that gap. It applies `tc netem` to an
interface, dispatches one command, restores the interface in a `finally`, and
then audits the ledger. The verdict is three invariants, each preventing a
specific failure:

| Invariant | The failure it prevents |
|---|---|
| `no_duplicate_effect` | a timed-out command dispatched twice — the robot moves twice |
| `uncertain_stays_unknown` | a timeout recorded as `failed`, which is the claim that invites the retry |
| `blocked_until_ack` | any side effect dispatched while an unresolved command is outstanding |

Five scenarios ship: `dropped_result`, `slow_ack`, `duplicated_packets`,
`bursty_loss`, `severe`. The rules, the command construction and the three
checks are unit-tested (19 tests, `agent_harness/tests/test_wire_faults.py`).

**NOT RUN.** It needs root for `tc`, a live stack, and a person — the command
moves the robot. `--dry-run` prints exactly what would be executed, which is
how the commands were reviewed without a machine to run them on.

Two limitations stated rather than hidden:

- **No port filter by default.** CycloneDDS picks ports dynamically, so a
  filter can silently miss traffic — and a filter that misses traffic produces
  a green result that proves nothing. The default covers the whole loopback
  interface, which on a dedicated test machine is acceptable and on a shared one
  is not.
- **The audit is post-hoc.** It reads final statuses and then the event order.
  That answers "did the system double-apply a command", which is the question
  the ledger exists for; it does not watch the run live.
