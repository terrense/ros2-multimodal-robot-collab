# Phase 1 Tasks

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## T1 — Spec and scaffolding

- [x] Write `docs/specs/phase1/spec.md` with the four Phase 0 open questions resolved
- [x] Create branch `phase1/contracts-v1` from `main` after Phase 0 merge
- [x] Create `agent_harness/contracts/` package skeleton

## T2 — Core types

- [x] `ids.py`: identifier helpers and validation
- [x] `vocab.py`: units, frames, risk-to-resource mapping
- [x] `status.py`: `ExecutionStatus`, transition table, `FailureCode`
- [x] `types.py`: `ActionRequest`, `ExecutionReceipt`, `ObservationSnapshot`,
      `TaskOutcome`, `CapabilityDescriptor`
- [x] `schema.py`: JSON Schema documents for each type
- [x] Unit tests for construction and validation

## T3 — Adapters

- [x] `adapters.py`: `agent_env.protocol` Action/StepResult ↔ contract
- [x] `adapters.py`: ROS 2 goal-status ints → `ExecutionStatus`
- [x] `capabilities.py`: registry loading from `skills/*/schema.json` and
      `agent_env/schemas/agent_skills.json`
- [x] Tests for backward compatibility and unsupported capabilities

## T4 — Verification

- [x] `tests/test_contracts.py` covering P1-R1..P1-R7 (37 tests)
- [x] `jsonschema` added to `requirements-dev.txt`
- [x] `ruff check .` clean
- [x] `pytest` green: 193 passed, 3 skipped (163 baseline + 30 new)
- [x] `pytest agent_harness/tests` green: 19 passed
- [x] ROS 2 build status recorded: **NOT RUN on this branch**. Phase 1 changes
      no file under `src/`, `scripts/` or `Dockerfile` (`git diff --name-only
      main...HEAD` is evidence), so the last green `ros2-build` (CI run
      `42478137319`) still describes the ROS 2 tree. The contract package is
      plain Python outside the colcon workspace.

## T5 — Documentation and hand-off

- [x] `docs/openrua_audit/contracts-v1.md`
- [x] Correct the Phase 0 documents where Phase 1 measurement disproved them
      (skills declared 8 / callable 6, not 9; registry union is 14)
- [x] State explicitly what Phase 1 does **not** yet guarantee
- [x] Commit in small steps; push branch
- [ ] Report to the user before Phase 2

## T6 — Independent review of the three open design decisions

- [x] Code review of `rejected` invariant, `units` shape, and UNKNOWN resolution
- [x] `rejected` kept, with the missing `True` and `from_dict` tests added, and
      the invariant mirrored into the JSON Schema
- [x] `units` kept as a mapping, keys constrained to top-level argument names,
      `m/s`, `rad/s`, `N`, `Nm` added
- [x] `reconciled: bool` replaced by `reconciled_by: <evidence id>` before it
      had any production caller
- [x] Full verification re-run after the changes

## Gate

- [x] No tracked change outside `agent_harness/contracts/`, `tests/`,
      `requirements-dev.txt`, `docs/`
- [x] Phase 2 proposal sketched (not implemented)

## Phase 2 sketch (not started)

Scope from the master plan, restated against what Phase 1 actually built:

1. Introduce a model-neutral `ToolClient` around the existing `SkillRegistry`
   and `RosToolExecutor`; keep both CLIs and dry-run behaviour intact.
2. Route every submission through `ActionRequest` → `ExecutionReceipt`, so the
   plan macro and any future step policy share one path.
3. Per-resource FIFO (`arm`, `base`) with queued-only cancellation; document
   that the ROS 2 action server owns sequencing so we do not add a second
   scheduler for it.
4. Bounded waits and per-command timeout propagation from
   `CapabilityDescriptor.timeout_s` (today a single global `--timeout-sec`).
5. A deterministic fake backend so every failure path is testable without ROS:
   unknown capability, invalid schema, resource collision, FIFO order, cancel
   queued, timeout, handler exception, shutdown with in-flight work.
6. Explicit ownership note: receipts are evidence; `MissionStateMachine` keeps
   owning mission state until Phase 3 introduces the ledger.

Out of scope for Phase 2: the ledger, reconciliation and the live ROS 2 adapter
(Phase 3); observation snapshots (Phase 4).
