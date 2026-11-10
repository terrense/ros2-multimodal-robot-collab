# agent_env — trainable embodied-agent environment layer

Closed-loop, step-level environment for SFT / DPO / step-level PPO / OPD on the
tool-delivery task. Replaces the open-loop "one blind mission plan" flow in
`agent_harness/` with: Observation → one skill call → structured feedback.

The post-training stack built on top of it lives in
[`training/`](../training/README.md).

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

## A note on the no-progress signal

`_signature()` is the state fingerprint behind `no_progress_count`. It includes
`occlusion_cleared` / `low_conf_cleared` because `navigation.reposition` — the
primary recovery for a blocked view — changes nothing else visible in the
fingerprint. Without them the correct recovery action is scored as making no
progress, and the reward built on top penalises exactly the behaviour the
training is meant to produce. This was found by evaluation, not by inspection:
the scripted expert, which recovers from 100% of injected faults, was being
flagged as stalled on a third of its episodes. See `docs/post_training.md` §7.

## Calibration against the live stack

L1's fault parameters are configured, not measured, and
[`l2_calibration.py`](l2_calibration.py) is where that stops being implicit:

- the **L1 side** of the comparison is derived from the configuration itself —
  the four stochastic families are estimated by drawing `default_params`, the
  deterministic ones follow from the simulator's semantics — so the reference
  cannot drift from the code that builds the scenarios;
- the **L2 side** is written down per family: the programme to run on Gazebo,
  what counts as a success, and what has to be arranged in the scene first;
- the manifest (`benchmarks/calibration_v1.json`) says `calibrated: false`
  whenever fewer than 30 samples back a family or their interval does not
  cover the L1 value.

```bash
python -m agent_env.scripts.calibrate_l2 --plan    # what to measure, per family
python -m agent_env.scripts.calibrate_l2 --l1      # what L1 predicts, and why
```

The committed manifest currently reports `not_measured` for all nine families:
the live stack has not been measured yet, and `docs/results.md` says so in its
header.

## Pending

`gazebo_adapter.py`, wrapping `agent_harness/scripts/ros_tool_executor.py` into
the same `env.step()` interface, for L2 (Gazebo) calibration and evaluation. The
calibration contract and the manifest writer exist; what is missing is the
runner that executes the per-family programmes on the live stack, and the
Gazebo scenes that make occlusion, a dropped tool and a blocked handover
reproducible.
