# Roadmap

## Phase 0: Scaffold

- Define ROS2 package boundaries.
- Define Actions, Services, and Topics.
- Provide simulated action servers for the full delivery loop.
- Document Agent skills and multi-agent ownership.
- Add third-party source integration for YOLOv8 and VINS-Mono.
- Add gesture-to-arm control commands for start, pause, and stop.

## Phase 1: Single-Robot Lab Demo

- Integrate Nav2 with a saved SLAM map.
- Add station registry and TF frames.
- Add OpenCV/YOLO tool detector.
- Connect YOLOv8 detections with RGB-D depth and camera-to-arm TF.
- Add arm pick/place adapter in simulation.
- Add basic ASR/TTS loop.
- Feed VINS-Mono VIO pose into mobile robot localization.

## Phase 2: Real Hardware

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
