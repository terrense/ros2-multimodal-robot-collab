# Roadmap

Three lines of work live in this repository. They have each used "Phase N", so
every phase reference below carries the prefix of its line. The full map is in
[`docs/specs/README.md`](specs/README.md).

```
┌─ R  Robot body ────────────── src/robot_collab_* ────────────────────────┐
│  the ROS 2 robot: sensors, arms, navigation, simulation, then hardware    │
│  R0 scaffold · R1 lab demo · R2 real hardware · R3 multi-agent ·          │
│  R4 post-training (the PT line below)                                     │
└───────────────────────────────────────────────────────────────────────────┘
┌─ PT Post-training ────────── agent_env/ + training/ ──────────────────────┐
│  the learning stack: an L1 simulator, rewards, credit assignment, and the │
│  SFT / DPO / PPO / OPD entry points                                        │
│  PT0 environment · PT1 L1 acceptance · PT2 workspace & CI · PT3 env       │
│  integration · PT4 training stack                                          │
│  ▸ blocked on GPU: the six experiment arms stay `pending`                  │
└───────────────────────────────────────────────────────────────────────────┘
┌─ O  OpenRUA assimilation ─── agent_harness/ ─────────────────────────────┐
│  absorb the discipline of Open-RUA/uni-robot-agent: contracts, a reliable │
│  tool runtime, and evidence for every side effecting command               │
│  O0 audit · O1 contract · O2 tool runtime · O3 ledger · O4 snapshots ·      │
│  O5 agent loop · O6 evaluation alignment · O7 training integration         │
│  ▸ open: O3.1 ROS concurrency, O3.1 wire faults, O6 L2 adapter,           │
│    O6 receipt metrics in the evaluator, O6 calibration runner              │
└───────────────────────────────────────────────────────────────────────────┘
```

## R — Robot body

Full detail: the R phases below. The current state of the simulation is in
[`docs/openrua_audit/gazebo-l2.md`](openrua_audit/gazebo-l2.md) and
[`docs/openrua_audit/sensors-and-slam.md`](openrua_audit/sensors-and-slam.md).

### R0: Scaffold — done

- ROS 2 package boundaries, actions/services/topics, simulated action servers.
- Vendored YOLOv8, OpenPose and VINS-Mono.
- Gesture-driven arm start/pause/stop.
- Agent harness schemas, examples and scripts.

### R1: Single-robot lab demo — done (in simulation)

- Gazebo Classic lab world, visible mobile base, lidar, RGB camera, IMU.
- Nav2 with a saved SLAM map, station registry and TF frames.
- OpenCV/YOLO tool detector with RGB-D depth and camera-to-arm TF.
- OpenPose gesture keypoints behind the safety policy.
- Arm pick/place adapter, ASR/TTS loop.
- VINS-Mono VIO into mobile-robot localization.

### R2: Real hardware — open, and separately authorized

- Replace the Gazebo odometry stand-in with `/vins_estimator/odometry`.
- Decide whether VINS feeds `robot_localization`, Nav2 localization, or a
  dedicated `map -> odom` estimator.
- Calibrate camera-to-base and camera-to-arm transforms.
- Add gripper state feedback and force/torque stop logic.
- Navigation recovery behaviours, operator registry, face-embedding store.
- Record rosbag datasets for regression testing.

### R3: Multi-agent planning — open

- Run the skill router with an LLM/VLM planner, with plan validation and a
  safety preflight.
- Let PerceptionAgent request new viewpoints when confidence is low.
- Let ManipulationAgent choose a grasp strategy from tool geometry.
- Multi-robot task allocation when a second base exists.

### R4: Post-training — see the PT line below.

## PT — Post-training

Design decisions: [`post_training.md`](post_training.md).
Engineering guide: [`../training/README.md`](../training/README.md).
Measured results: [`results.md`](results.md).

### Done

- Closed-loop agent environment: observation/action protocol, action registry,
  nine fault families with compatible double-fault pairs, the L1 text-level
  simulator and the scripted expert (`agent_env/`).
- One prompt surface shared by data generation, training and evaluation, with
  format-tolerant / content-strict action parsing (`training/chat_format.py`).
- Composite step reward with ablation presets, and turn-level credit
  assignment that scatters GAE advantages onto the action token spans that
  earned them — plus the flat-credit baseline as a runnable comparison.
- Datasets: SFT corpus, DPO pairs whose preference direction is verified by
  executing the alternative in a cloned environment, and a frozen 400-episode
  held-out benchmark with enforced seed and operator-pool isolation.
- Policies: rule baseline (the deployed system reconstructed, with its coverage
  gaps explicit), expert oracle, vLLM/HF backends, frozen OPD teacher with a
  budgeted turn gate.
- Training entry points for SFT, DPO, step-level PPO and OPD (DeepSpeed
  ZeRO-3), a memory pre-flight that refuses to start a rollout engine that
  cannot fit, and a served-teacher backend for OPD.
- Evaluation with a failure taxonomy, bucket breakdowns and a reward-hacking
  audit; the report generator renders measured values against pre-registered
  acceptance targets and refuses to drop a published measurement.

### Open

- Run the six experiment arms on GPUs and fill in `results.md`. Blocked on
  hardware, not on code: see the memory budget in
  [`post_training.md`](post_training.md) §15.
- `agent_env/l2_gazebo.py`: expose the live ROS 2 / Gazebo stack behind the same
  `env.step()` interface, for L2 calibration and evaluation. (This is the **O6**
  leftover — the adapter belongs to the OpenRUA line, and PT consumes it.)
- Calibrate L1's fault behaviour against measured Gazebo outcomes, so the fast
  simulator's failure rates match the live stack's. The contract and the L1
  reference values exist; the runner and the scenes do not.
- Real-robot regression pass on the arms that survive simulation.

## O — OpenRUA assimilation

Master plan: [`plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md).
Audit and adoption decisions: [`openrua_audit/`](openrua_audit/).

The upstream contributes no training code — it is an agent orchestration
framework. What it contributes is the discipline this repository was missing:
a command has an identity, a side effecting call leaves evidence, an
uncertain outcome is reconciled before the next command, and two mutually
exclusive resources are never run at the same time.

### Done — O0 … O7

See [`specs/README.md`](specs/README.md) for the phase-by-phase table.

### Open — the rest of the line

| Item | Phase | Why it matters |
|---|---|---|
| MultiThreadedExecutor + per-resource callback groups | O3.1 | The arm and the base are different resources and should be able to move at once; today the ROS side serialises everything, so cross-resource concurrency is only proved against the fake backend. |
| Fault injection on the wire (DDS-level drops) | O3.1 | The eight transport faults are injected in-process. The real question is whether a live stack under packet loss still refuses to double-apply a command. |
| `agent_env/l2_gazebo.py` — the L2 adapter | O6 | One `reset()`/`step()` interface over the live stack, so the same evaluator that scores L1 can score L2, and the receipt metrics become reachable. |
| Receipt metrics wired into the evaluator | O6 | `training/receipt_metrics.py` computes uncertain-outcome rate, duplicate-effect rate and latency; the evaluator never calls them because L1 episodes carry no receipts. |
| The L2 calibration runner and its scenes | O6 | Per-family programmes on Gazebo, plus scenes that make occlusion, a dropped tool and a blocked handover reproducible. This is what turns L1's fault parameters from assumptions into measurements. |

### The rule that governs all of it

Emergency stop and safety bounds are controller and hardware concerns. The LLM
may propose an action; it may never widen a bound. A command with an uncertain
physical effect is reconciled, never replayed. And nothing is reported as a
result that is not backed by an artifact.
