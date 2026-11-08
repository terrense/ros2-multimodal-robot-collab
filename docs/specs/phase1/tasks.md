# Phase 1 Tasks

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## T1 — Spec and scaffolding

- [x] Write `docs/specs/phase1/spec.md` with the four Phase 0 open questions resolved
- [x] Create branch `phase1/contracts-v1` from `main` after Phase 0 merge
- [ ] Create `agent_harness/contracts/` package skeleton

## T2 — Core types

- [ ] `ids.py`: identifier helpers and validation
- [ ] `status.py`: `ExecutionStatus`, transition table, `FailureCode`
- [ ] `types.py`: `ActionRequest`, `ExecutionReceipt`, `ObservationSnapshot`,
      `TaskOutcome`, `CapabilityDescriptor`
- [ ] `schema.py`: JSON Schema documents for each type
- [ ] Unit tests for construction and validation

## T3 — Adapters

- [ ] `adapters.py`: `agent_env.protocol` Action/StepResult ↔ contract
- [ ] `adapters.py`: ROS 2 goal-status ints → `ExecutionStatus`
- [ ] Capability registry loading from `skills/*/schema.json` and
      `agent_env/schemas/agent_skills.json`
- [ ] Tests for backward compatibility and unsupported capabilities

## T4 — Verification

- [ ] `tests/test_contracts.py` covering P1-R1..P1-R7
- [ ] `jsonschema` added to `requirements-dev.txt`
- [ ] `ruff check .` clean
- [ ] `pytest` green (163 existing + new)
- [ ] `pytest agent_harness/tests` green
- [ ] ROS 2 build status recorded (CI or NOT RUN)

## T5 — Documentation and hand-off

- [ ] `docs/openrua_audit/contracts-v1.md`
- [ ] Update `03_gap_matrix.md` rows that Phase 1 closes
- [ ] State explicitly what Phase 1 does **not** yet guarantee
- [ ] Commit in small steps; push branch
- [ ] Report to the user before Phase 2

## Gate

- [ ] No tracked change outside `agent_harness/contracts/`, `tests/`,
      `requirements-dev.txt`, `docs/`
- [ ] Phase 2 proposal sketched (not implemented)
