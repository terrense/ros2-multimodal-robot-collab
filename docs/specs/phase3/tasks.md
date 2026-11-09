# Phase 3 Tasks

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## T0 — Approval gate

- [x] Write `docs/specs/phase3/spec.md`
- [ ] User approves the spec and answers the five open questions
- [ ] No implementation before that

## T1 — Contract v1.1 (additive)

- [ ] `ExecutionReceipt.backend_ref: str | None`
- [ ] `FailureCode`: `duplicate_command`, `stale_state`, `not_reconciled`, `stop_timeout`
- [ ] Backward compatibility test: a v1 receipt without `backend_ref` still parses
- [ ] Schema documents updated; existing 225 tests stay green

## T2 — Ledger

- [ ] `agent_harness/runtime/ledger.py` on stdlib `sqlite3`
- [ ] Schema exactly as in the spec; one transaction per event
- [ ] `record_submitted`, `record_started`, `record_finished`,
      `record_event`, `orphans`, `acknowledge_unknown`
- [ ] Duplicate `command_id` raises rather than updating
- [ ] Crash test: row survives a killed process

## T3 — Reconciliation

- [ ] State machine as specified; `unknown` only leaves with evidence
- [ ] Handle-alive resolution path
- [ ] Restart marks orphans; side-effecting submits refused while unresolved
- [ ] Operator acknowledgement CLI (`--note` required, recorded as `operator:<name>`)

## T4 — ROS 2 adapter

- [ ] `agent_harness/runtime/ros_adapter.py`: MultiThreadedExecutor,
      per-resource callback groups, reused action clients
- [ ] Capture `goal_uuid` after acceptance; write correlation before waiting
- [ ] Feedback recorded, never terminal-state-changing
- [ ] Stale `state_version` gate
- [ ] Bounded `wait_for_server` and result waits

## T5 — Control paths

- [ ] `cancel` (action cancel, 2 s ack)
- [ ] `pause` (arm control command)
- [ ] `stop` (topic + fail-closed flag), labelled not-an-E-stop in help and docs
- [ ] All three callable from the CLI without any model

## T6 — Fault injection

- [ ] The eight faults from the spec matrix, against the fake environment
- [ ] `docs/openrua_audit/fault-injection.md` with the result of each
- [ ] State machine diagram verified against the tests

## T7 — Verification and hand-off

- [ ] Dry-run golden + 19 harness tests unchanged
- [ ] `ruff` clean, full pytest green (225 + new)
- [ ] `--engine legacy` still works
- [ ] ROS 2 build status recorded; hardware NOT RUN
- [ ] Report before Phase 4

## Gate

- [ ] No change to any ROS 2 node's control logic
- [ ] No new runtime dependency
- [ ] No claim of `physical_effect_verified=True` without observation
