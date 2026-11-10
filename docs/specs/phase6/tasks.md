# Phase 6 Tasks

> **OpenRUA assimilation — Phase O6.** Not to be confused with the post-training
> line (`PT6` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R6` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

Status as of 2026-11-10: the code is done; what is missing is the live stack
running under it.

- [x] User approves the spec and the four recommendations
- [x] `agent_harness/runtime/l1_backend.py`: L1 env → receipts, same shape as ROS
- [x] `agent_env/l2_gazebo.py`: L2 adapter (reset/step over ToolClient, lazy ROS) — the live stack behind `reset()`/`step()`, with the action mapping written out (`ACTION_MAP`) and the actions the live stack cannot do refused with a reason. The ROS import is lazy, so the mapping and the result logic are tested without ROS 2.
- [x] `training/evaluate_l2.py`: the live stack scored by the L1 evaluator — same `evaluate_records`, same report generator, same run records, plus the receipt metrics, which only a live mission can produce.
- [x] `agent_env/scripts/calibrate_l2.py`: per-family measurement + Wilson interval — the measurement contract, the L1 reference rates and the manifest writer are done (`agent_env/l2_calibration.py`).
- [x] `agent_env/calibration_runner.py` + `agent_env/scripts/run_calibration.py`: the runner that executes the programmes on the live stack — five families have probes that work by calling the stack; four need the scene arranged and are reported as unmeasured with the reason. **The ROS path is NOT RUN**: no stack was up.
- [x] `benchmarks/calibration_v1.json` writer, `calibrated: false` under 30 samples — the committed manifest says `not_measured` for all nine families, which is the truth: no family has been measured on the live stack yet, and the report header says so.
- [x] `training/evaluate.py`: receipt metrics (uncertain, duplicate, latency, cost) — computed on the live stack by `training/evaluate_l2.py`. The L1 evaluator scores trajectories, which carry no receipts; that is why the metrics belong to the L2 path.
- [x] `training/ablation.py`: condition table, equal-budget enforcement
- [x] Ground-truth leak test across all nine fault families
- [x] Run record: manifest hash + seed + budgets
- [x] Frozen benchmark hash test
- [x] Full suite green (384 at the time; 412 now)
- [x] Report before Phase 7

## What is still open

The **Gazebo scenes** that make occlusion, a dropped tool, a blocked handover
and a mid-motion cancel reproducible. Five of the nine families have probes
that work by calling the stack (`nav_timeout`, `grasp_failure`,
`low_confidence`, `identity_mismatch`, `tool_missing`); four need the world
arranged first, and the runner reports them as unmeasured with the reason
rather than filling the gap.

The calibration contract and the runner exist
(`agent_env/l2_calibration.py`, `agent_env/calibration_runner.py`). What is
missing is the scene files -- and an operator to run them, because those
probes move the robot.

```bash
python -m agent_env.scripts.run_calibration --plan          # what would run
python -m agent_env.scripts.run_calibration --out runs/l2_measurements.json
python -m agent_env.scripts.calibrate_l2 \
    --from-log runs/l2_measurements.json --out benchmarks/calibration_v1.json
```
