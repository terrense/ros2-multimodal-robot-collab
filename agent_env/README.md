# agent_env — trainable embodied-agent environment layer

Closed-loop, step-level environment for SFT / DPO / step-level PPO on the
tool-delivery task. Replaces the open-loop "one blind mission plan" flow in
`agent_harness/` with: Observation → one skill call → structured feedback.

| Module | Role |
|---|---|
| `protocol.py` | Observation/Action dataclasses, phase ladder (progress estimator backbone), `ActionRegistry` loading `schemas/agent_skills.json` (13 step-level actions, risk levels, confirmation flags) |
| `faults.py` | 9 fault families (occlusion, low confidence, tool missing, nav timeout, grasp failure, tool drop, identity mismatch, no progress, user cancel) with randomized per-episode params and compatible double-fault pairs |
| `l1_sim.py` | Text-level fast simulator (`L1DeliveryEnv`): world logic mirrors `mission_state_machine.py`; ~ms per step; emits per-step reward components (progress / grounding / safety / cost / success) |
| `expert_policy.py` | Scripted expert with one hand-written recovery rule per fault family; SFT teacher |
| `trajectory_logger.py` | JSONL trajectory protocol shared by SFT / DPO / rollout / eval |
| `scripts/collect_trajectories.py` | Batch collection + Phase-0 acceptance report |

Quick start (pure stdlib, run from repo root):

```bash
python -m agent_env.scripts.collect_trajectories --acceptance --out data/l1_acceptance.jsonl
python -m agent_env.scripts.collect_trajectories --mix --episodes 10000 --out data/sft_raw.jsonl
```

Pending: `gazebo_adapter.py` wrapping `agent_harness/scripts/ros_tool_executor.py`
into the same `env.step()` interface for L2 (Gazebo) calibration and evaluation.
