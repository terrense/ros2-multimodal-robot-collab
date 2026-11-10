# Phase 1 Spec — Versioned Step Contract

> **OpenRUA assimilation — Phase O1.** Not to be confused with the post-training
> line (`PT1` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R1` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

Status: **complete — merged to `main`**
Branch: `phase1/contracts-v1`
Master plan: [`../../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md)
Phase 0 decisions: [`../../openrua_audit/05_adoption_decisions.md`](../../openrua_audit/05_adoption_decisions.md) §5

## Goal

Introduce one typed, ROS 2-neutral step contract that both execution paths can
speak, so that a command and its outcome are uniquely identified, an unknown
physical outcome is representable, and "the call worked" is never confused with
"the robot did it" or "the task succeeded".

This phase is additive. It changes no behaviour and no existing schema.

## Decisions carried in from Phase 0 (open questions resolved)

| Question | Decision | Reason |
|---|---|---|
| Where does the contract live? | `agent_harness/contracts/` | `agent_env/` is frozen for training; `src/` must stay ROS-only |
| Validation dependency | stdlib `dataclasses` at runtime, `jsonschema` in tests only | keeps the ROS 2 image free of new runtime dependencies |
| Frame/unit vocabulary | frame `map` for spatial arguments; units from `{"m", "rad", "s", "m/s", "rad/s", "N", "Nm", "unitless"}`, keys are top-level argument names | one canonical vocabulary, rejected at parse time otherwise |
| Does `partial` exist? | Not as a status in v1 | represented as `FAILED` + `physical_effect_verified=True`; a status value we cannot populate is a lie waiting to happen |

## Contract v1

### Identifiers

- `episode_id` — one run of one task; stable across all commands in it.
- `command_id` — idempotency identity of a single submission. A retry of the
  same intent reuses it; a new command never does.
- `snapshot_id` — content identity of an observation.
- `state_version` — monotonically increasing view of the robot state.

### ExecutionStatus

`not_started | queued | running | completed | failed | rejected | unknown`

`rejected` means refused before any effect. `unknown` means the effect cannot be
established — it is not a synonym for failure. Legal transitions:

```
not_started -> queued | running | rejected
queued      -> running | rejected | unknown
running     -> completed | failed | unknown
unknown     -> completed | failed        (only with reconciled_by=<evidence id>)
completed / failed / rejected            (terminal)
```

### The three-way split

| Field | Question answered | Never means |
|---|---|---|
| `call_ok` | did the invocation produce a well-formed result? | motion, grasp, delivery |
| `physical_effect_verified` | did the world observably change as requested? | task success |
| `TaskOutcome.success` | did the episode achieve its goal? | that the robot did anything |

`physical_effect_verified` is tri-state: `None` = not established.

### Types

- `ActionRequest` — episode_id, command_id, capability, arguments,
  requested_at, frame_id, units, state_version, resource_key, timeout_s.
- `ExecutionReceipt` — episode_id, command_id, execution_status, call_ok,
  physical_effect_verified, started_at, finished_at, failure_code, detail,
  snapshot_id, state_version, resource_key, result (raw payload preserved).
- `ObservationSnapshot` — snapshot_id, taken_at, frame_id, units, state_version,
  payload.
- `TaskOutcome` — success (tri-state), accepted, retryable, failure_category;
  invariant: a retryable outcome must not be accepted (it is excluded from the
  evaluation denominator).
- `FailureCode` — closed enum; unknown codes are rejected at parse time.
- `CapabilityDescriptor` — name, granularity (`atomic|composite`), available_in
  (`l1|ros2`), required_args, mutates_world, resource_key.

## Adapters (no competing authority)

- `agent_env.protocol.Action` / `StepResult` ↔ contract. The L1 objects stay
  authoritative for L1; the adapter is a translation, not a second store.
- ROS 2 goal status (plain ints, no rclpy import) → `ExecutionStatus`, with the
  rule that `SUCCEEDED` yields `physical_effect_verified=None` — the action's
  own opinion is not physical verification.

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| P1-R1 | Every type round-trips through JSON without field loss | test |
| P1-R2 | Unknown units, frames and failure codes are rejected | test |
| P1-R3 | Illegal status transitions are rejected; `unknown → completed` requires a named reconciliation record | test |
| P1-R4 | All 13 L1 actions survive `Action → contract → Action` unchanged | test |
| P1-R5 | Every `skills/*/schema.json` action resolves to exactly one descriptor | test |
| P1-R6 | The 3 ROS-only actions are not exposed to L1 | test |
| P1-R7 | Schema documents validate instances produced by the dataclasses | test |
| P1-R8 | No change to `src/`, `agent_env/`, `training/`, `skills/` | `git status` |

## Non-goals

- Wiring the contract into the executor (Phase 2).
- Ledger, reconciliation and ROS 2 adapter behaviour (Phase 3).
- Observation snapshots from cameras/TF (Phase 4).
- Any change to the L1 action vocabulary or the frozen benchmark.
