# Roadmap

## Phase 0: Scaffold

- Define ROS2 package boundaries.
- Define Actions, Services, and Topics.
- Provide simulated action servers for the full delivery loop.
- Document Agent skills and multi-agent ownership.
- Add vendored third-party source integration for YOLOv8, OpenPose, and VINS-Mono.
- Add gesture-to-arm control commands for start, pause, and stop.
- Add Agent harness schemas, examples, and scripts.

## Phase 1: Single-Robot Lab Demo

- Add Gazebo Classic lab world, visible mobile base, lidar, RGB camera, and IMU.
- Launch the agent/mission stack beside the Gazebo robot.
- Route Gazebo odometry through the VINS bridge as a first visible localization stand-in.
- Integrate Nav2 with a saved SLAM map.
- Add station registry and TF frames.
- Dispatch station goals through Nav2 `NavigateToPose`.
- Add OpenCV/YOLO tool detector.
- Connect YOLOv8 detections with RGB-D depth and camera-to-arm TF.
- Connect OpenPose hand keypoints with gesture safety policy.
- Add arm pick/place adapter in simulation.
- Add basic ASR/TTS loop.
- Feed VINS-Mono VIO pose into mobile robot localization.

## Phase 2: Real Hardware

- Replace Gazebo odometry stand-in with VINS-Mono `/vins_estimator/odometry`.
- Decide whether VINS feeds `robot_localization`, Nav2 localization, or a dedicated `map -> odom` estimator.
- Calibrate camera-to-base and camera-to-arm transforms.
- Add gripper state feedback and force/torque stop logic.
- Add navigation recovery behaviors.
- Add operator registry and face embedding store.
- Record rosbag datasets for regression testing.

## Phase 3: Multi-Agent Planning

- Run the skill router with an LLM/VLM planner.
- Add plan validation and safety preflight.
- Let PerceptionAgent request new viewpoints when confidence is low.
- Let ManipulationAgent choose grasp strategy from tool geometry.
- Add multi-robot task allocation when more than one mobile base is available.

## Phase 4: Post-Training the High-Level Policy

Motivation and design decisions: [post_training.md](post_training.md).
Engineering guide: [../training/README.md](../training/README.md).

### Done

- Closed-loop agent environment: observation/action protocol, action registry,
  nine fault families with compatible double-fault pairs, L1 text-level
  simulator, scripted expert (`agent_env/`).
- Single prompt surface shared by data generation, training and evaluation, with
  format-tolerant / content-strict action parsing (`training/chat_format.py`).
- Composite step reward with ablation presets, and turn-level credit assignment
  that scatters GAE advantages onto the action token spans that earned them —
  plus the flat-credit baseline as a runnable comparison.
- Datasets: SFT corpus, DPO pairs whose preference direction is verified by
  executing the alternative in a cloned environment, and a frozen 400-episode
  held-out benchmark with enforced seed and operator-pool isolation.
- Policies: rule baseline (the deployed system reconstructed, with its coverage
  gaps made explicit), expert oracle, vLLM/HF backends, frozen OPD teacher with
  a budgeted turn gate.
- Training entry points for SFT, DPO, step-level PPO and OPD (DeepSpeed ZeRO-3).
- Evaluation with a failure taxonomy and bucket breakdowns; report generator
  that renders measured values against pre-registered acceptance targets.

### Next

- Run the five experiment arms on GPUs and fill in `results.md`.
- `agent_env/gazebo_adapter.py`: expose the live ROS2/Gazebo stack behind the
  same `env.step()` interface, for L2 calibration and evaluation.
- Calibrate L1 transition probabilities against measured Gazebo outcomes, so the
  fast simulator's failure rates match the real stack's.
- Real-robot regression pass on the arms that survive simulation.
