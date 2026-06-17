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
