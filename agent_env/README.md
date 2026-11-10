# agent_env — the training environment layer, and the bridge to the live stack

A closed-loop, step-level environment for SFT / DPO / step-level PPO / OPD on
the tool-delivery task: Observation → one skill call → structured feedback.

This is the **PT** line (post-training). The runtime that actually executes
skill calls lives in [`agent_harness/`](../agent_harness/), and the two meet in
`l2_gazebo.py`. The trainer built on top of this environment is in
[`training/`](../training/README.md).

Line prefixes and the full map: [`docs/specs/README.md`](../docs/specs/README.md).

## Modules

| Module | Role |
|---|---|
| `protocol.py` | Observation/Action dataclasses, phase ladder (the progress estimator's backbone), `ActionRegistry` over `schemas/agent_skills.json` (13 step-level actions, risk levels, confirmation flags) |
| `faults.py` | 9 fault families (occlusion, low confidence, tool missing, nav timeout, grasp failure, tool drop, identity mismatch, no progress, user cancel) with randomized per-episode params and compatible double-fault pairs |
| `l1_sim.py` | Text-level fast simulator (`L1DeliveryEnv`): world logic mirrors `mission_state_machine.py`, ~ms per step, emits per-step reward components (progress / grounding / safety / cost / success) |
| `expert_policy.py` | Scripted expert with one hand-written recovery rule per fault family; the SFT teacher |
| `trajectory_logger.py` | JSONL trajectory protocol shared by SFT / DPO / rollout / eval |
| `l2_gazebo.py` | The live ROS 2 / Gazebo stack behind the same `reset()` / `step()` shape, so one evaluator scores both levels |
| `calibration.py` | Wilson interval, `compare_family`, `build_manifest` — the statistics behind "calibrated" |
| `l2_calibration.py` | Per-family measurement contract: L1 reference rates derived from the configuration, and what has to be observed on the live stack |
| `calibration_runner.py` | Executes those programmes; five families by calling the stack, four refuse until their scene is arranged |
| `scripts/collect_trajectories.py` | Batch collection + the PT0 acceptance report |
| `scripts/calibrate_l2.py` | Print the programme / the L1 predictions, or build the manifest from a measurement log |
| `scripts/run_calibration.py` | Run the programmes against the live stack |

Quick start (pure stdlib, run from the repo root):

```bash
python -m agent_env.scripts.collect_trajectories --acceptance --out data/l1_acceptance.jsonl
python -m agent_env.scripts.collect_trajectories --mix --episodes 10000 --out data/sft_raw.jsonl
```

## L1 and L2 are not the same shape, and the code says so

L1 has 13 fine-grained actions and is text-level. The live stack exposes six
callable capabilities, two of them composite, and publishes detections rather
than answering detection requests. `l2_gazebo.py` writes that mapping out
explicitly:

- an L1 action with no live counterpart (`perception.request_tool_detection`,
  `navigation.reposition`, `hri.request_confirmation`, `mission.terminate`) is
  **refused with a reason**, not silently skipped — on the live stack "not
  supported" and "did nothing" are different failures, and the second one is how
  a robot moves twice;
- `holding_tool` and `operator_verified` live inside the mission state machine
  and are not published, so the observation says *unknown* rather than guessing:
  an unconfirmed physical effect is not a success, and an unknown is not a
  `False` a policy can act on;
- an L2 episode is one **mission** while an L1 episode is one **turn**, and the
  L2 report carries `unit: mission` so the two are never read as the same thing.

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
python -m agent_env.scripts.calibrate_l2 --plan      # what to observe, per family
python -m agent_env.scripts.calibrate_l2 --l1        # what L1 predicts, and why
python -m agent_env.scripts.run_calibration --plan   # what would run on the live stack
```

The committed manifest reports `not_measured` for all nine families: the live
stack has not been measured yet, and `docs/results.md` says so in its header.

## What is still open

| Item | Why it is open |
|---|---|
| Actually measuring on L2 | The runner exists and five families have probes that work by calling the stack. Nothing has been run: it moves the robot, so it needs an operator present and the stack up. |
| The four scene-dependent families | Occlusion, tool drop, blocked handover and mid-motion cancel need the world arranged. `scripts/calibration_scene.py` spawns and clears the props (and dispatches no robot skill), but no one has tried it in Gazebo yet. |

`user_cancel` has no props: the fault is an event, not an arrangement, and the
script prints the manual steps instead.
