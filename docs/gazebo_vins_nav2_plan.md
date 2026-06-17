# Gazebo, Nav2, and VINS-Mono Plan

## Goal

Make the agent-driven robot collaboration demo visible and physical in simulation:

```text
LLM / Agent tool call
  -> ROS2 mission action
  -> station navigation skill
  -> Nav2 NavigateToPose
  -> Gazebo mobile base motion
  -> odometry / VINS pose feedback
```

The long-term target is to replace Gazebo odometry with VINS-Mono visual-inertial odometry while keeping the same ROS2 skill contracts.

## Current Implementation

- `robot_collab_sim` provides a Gazebo Classic lab world and a small differential-drive mobile base.
- The mobile base publishes `/odom`, `/scan`, `/camera/color/image_raw`, and `/imu/data`.
- `gazebo_nav_vins_demo.launch.py` starts Gazebo, the mission stack, the agent gateway, simulated perception, and the VINS bridge.
- `vins_mono_bridge_node` subscribes to `/odom` in the first Gazebo demo and republishes `/slam/vins_pose`.
- `nav_skill_server` now supports:
  - `backend: simulated` for deterministic API demos.
  - `backend: nav2` for dispatching `nav2_msgs/action/NavigateToPose`.
- Station goals are resolved from `robot_collab_bringup/config/stations.yaml`.

## Important Boundary

VINS-Mono gives visual-inertial odometry or pose. It does not by itself provide the 2D occupancy map that Nav2 global planning needs.

For a vivid Gazebo navigation demo, use:

- `/odom` or VINS-Mono-derived odometry for local motion feedback.
- `/scan` from the Gazebo lidar for Nav2 costmaps.
- a saved map, SLAM Toolbox map, or a simple lab map for global planning.
- a consistent TF chain, usually `map -> odom -> base_footprint -> base_link`.

The current bridge publishes `/slam/vins_pose` first and keeps TF publishing disabled in Gazebo to avoid conflicting with the diff-drive odometry TF. The next VINS step should decide whether VINS feeds `robot_localization`, Nav2 localization, or a dedicated `map -> odom` estimator.

## Implementation Order

1. Visible Gazebo base
   - Launch the lab world and spawn the robot.
   - Verify `/cmd_vel` moves the base.
   - Verify `/odom`, `/scan`, `/camera/color/image_raw`, and `/imu/data`.

2. Station navigation contract
   - Keep `/skills/navigate_to_station` as the agent-facing API.
   - Resolve `station_id` to pose from `stations.yaml`.
   - Use `backend:=simulated` for agent demos and `backend:=nav2` for real Nav2 dispatch.

3. Nav2 closure
   - Add or select a Nav2 params file for the Gazebo base.
   - Start planner, controller, behavior server, BT navigator, and costmaps.
   - Validate `ros2 action send_goal /navigate_to_pose ...` moves the robot.
   - Then validate `/skills/navigate_to_station` moves the robot to `station_a`.

4. VINS-Mono closure
   - First use Gazebo `/odom` through `vins_mono_bridge_node` as a stand-in.
   - Then connect VINS-Mono image and IMU inputs from Gazebo or a rosbag.
   - Publish VINS odometry to `/vins_estimator/odometry`.
   - Fuse or expose the result without breaking Nav2 TF.

5. Agent harness
   - Generate function-calling tools from the skill registry.
   - Validate tool calls before dispatch.
   - Keep LLM planning above deterministic ROS2 mission and safety nodes.

## First Demo Command

```bash
ros2 launch robot_collab_bringup gazebo_nav_vins_demo.launch.py
```

With a running Nav2 stack:

```bash
ros2 launch robot_collab_bringup gazebo_nav_vins_demo.launch.py nav_backend:=nav2
```

