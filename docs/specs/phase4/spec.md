# Phase 4 Spec — Observation Snapshots and Capability Discovery

> **OpenRUA assimilation — Phase O4.** Not to be confused with the post-training
> line (`PT4` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R4` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

Status: **complete — merged to `main`**
Branch: `phase4/observation-snapshots`
Master plan: [`../../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md) §Phase 4

## Goal

Give the agent a world view it can trust: snapshots that carry when they were
taken, what calibrated them, which frame they are in and which state version
they describe — and a capability list derived from the registry rather than
from a hand-written prompt.

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| P4-R1 | A snapshot carries `sensor_id`, `calibration_version`, `frame_id`, `taken_at`, `state_version` | schema + round-trip test |
| P4-R2 | Stale snapshots are rejected before a side-effecting action | `require_fresh` test |
| P4-R3 | A snapshot with a mismatched frame or unknown calibration is rejected | validation test |
| P4-R4 | Model-visible capabilities are rendered from `CapabilityRegistry`, not a hardcoded list | every registered ROS 2 capability appears; non-callable ones do not |
| P4-R5 | Evaluator-only state never reaches a model-facing snapshot | renderer test |
| P4-R6 | Existing behaviour unchanged: 251 tests stay green | test run |

## Contract additions (v1.2, additive)

- `ObservationSnapshot.sensor_id` — which source produced it (`d435`, `overhead`, `velodyne`).
- `ObservationSnapshot.calibration_version` — opaque token identifying the calibration set.
- `age(now_s)` / `is_fresh(now_s, max_age_s)` / `require_fresh(now_s, max_age_s)`.
- `StaleSnapshot` error for the gate.

## Capability description

`CapabilityRegistry.describe_for(platform)` renders one line per capability:
`name(required_args) [granularity, risk]`. The model-facing prompt uses this;
adding an action to a schema file makes it appear without touching code.

## Non-goals

- No camera/TF plumbing in this phase (the snapshot *contract* only).
- No change to the L1 action vocabulary or the frozen benchmark.
- No change to ROS 2 nodes.
