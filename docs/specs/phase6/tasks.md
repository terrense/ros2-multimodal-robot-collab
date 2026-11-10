# Phase 6 Tasks

Status as of 2026-11-10: everything here is implemented except the two marked
*not done* — and both of those need the live stack, not more code.

- [x] User approves the spec and the four recommendations
- [x] `agent_harness/runtime/l1_backend.py`: L1 env → receipts, same shape as ROS
- [ ] `agent_env/l2_gazebo.py`: L2 adapter (reset/step over ToolClient, lazy ROS) — **not done**. The live stack is driven today through `agent_harness/scripts/ros_tool_executor.py`; there is no adapter that presents it as a `reset()/step()` environment.
- [x] `agent_env/scripts/calibrate_l2.py`: per-family measurement + Wilson interval — the measurement contract, the L1 reference rates and the manifest writer are done (`agent_env/l2_calibration.py`); the live runner that executes the programmes on Gazebo is not.
- [x] `benchmarks/calibration_v1.json` writer, `calibrated: false` under 30 samples — the committed manifest says `not_measured` for all nine families, which is the truth: nothing has been measured on L2 yet, and the report header now says so.
- [x] `training/evaluate.py`: receipt metrics (uncertain, duplicate, latency, cost) — `training/receipt_metrics.py` computes them, but the evaluation does not call them: it scores L1 episodes, which carry no receipts. They become reachable once an L2 adapter feeds the evaluator a receipt stream.
- [x] `training/ablation.py`: condition table, equal-budget enforcement
- [x] Ground-truth leak test across all nine fault families
- [x] Run record: manifest hash + seed + budgets
- [x] Frozen benchmark hash test
- [x] Full suite green (361)
- [x] Report before Phase 7
