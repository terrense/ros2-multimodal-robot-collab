#!/bin/bash
# Stop everything started by scripts/run_gazebo_demo.sh.
pkill -INT -f "ros2 launch robot_collab_bringup gazebo_nav_vins_demo" 2>/dev/null
sleep 3
for pattern in gzserver gzclient bt_navigator controller_server planner_server \
               behavior_server smoother_server velocity_smoother lifecycle_manager \
               waypoint_follower slam_toolbox async_slam_toolbox_node \
               static_transform_publisher vins_mono_bridge_node \
               robot_state_publisher mission_state_machine nav_skill_server \
               arm_skill_server tool_detector_node yolov8_tool_detector_node \
               face_auth_node gesture_recognizer_node voice_gateway_node \
               agent_gateway_node spawn_entity; do
  pkill -9 -f "$pattern" 2>/dev/null
done
sleep 1
echo "remaining=$(pgrep -c -f 'robot_collab|gzserver|slam_toolbox|static_transform_publisher' 2>/dev/null || echo 0)"
echo "stopped"
