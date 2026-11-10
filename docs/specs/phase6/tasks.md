# Phase 6 Tasks

- [ ] User approves the spec and the four recommendations
- [ ] `agent_harness/runtime/l1_backend.py`: L1 env → receipts, same shape as ROS
- [ ] `agent_env/l2_gazebo.py`: L2 adapter (reset/step over ToolClient, lazy ROS)
- [ ] `agent_env/scripts/calibrate_l2.py`: per-family measurement + Wilson interval
- [ ] `benchmarks/calibration_v1.json` writer, `calibrated: false` under 30 samples
- [ ] `training/evaluate.py`: receipt metrics (uncertain, duplicate, latency, cost)
- [ ] `training/ablation.py`: condition table, equal-budget enforcement
- [ ] Ground-truth leak test across all nine fault families
- [ ] Run record: manifest hash + seed + budgets
- [ ] Frozen benchmark hash test
- [ ] Full suite green (281+)
- [ ] Report before Phase 7
