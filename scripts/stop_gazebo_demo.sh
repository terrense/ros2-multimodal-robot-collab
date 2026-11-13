#!/bin/bash
# Stop everything started by scripts/run_gazebo_demo.sh.
#
# Two passes, and the order matters.
#
# The first pass signals the whole process group of the launch. The demo
# starts the launch with setsid, so the launch leads its own group and the
# nodes belong to it: signalling the launch alone leaves them behind, and a
# session that restarts the demo a few times ends up with a dozen silent
# nodes eating memory.
for pgid in $(pgrep -f "ros2 launch robot_collab_bringup" | xargs -r ps -o pgid= | tr -d ' ' | sort -u); do
  kill -INT -- -"$pgid" 2>/dev/null
done
pkill -INT -f "ros2 launch robot_collab_bringup gazebo_nav_vins_demo" 2>/dev/null
sleep 3
# The second pass is for whatever the first one missed: a node that was
# started outside the launch's group, or a launch that is already gone.
for pattern in gzserver gzclient bt_navigator controller_server planner_server \
               behavior_server smoother_server velocity_smoother lifecycle_manager \
               waypoint_follower slam_toolbox async_slam_toolbox_node \
               static_transform_publisher vins_mono_bridge_node \
               robot_state_publisher mission_state_machine nav_skill_server \
               arm_skill_server tool_detector_node yolov8_tool_detector_node \
               face_auth_node gesture_recognizer_node voice_gateway_node \
               agent_gateway_node zone_occupancy_node spawn_entity; do
  pkill -9 -f "$pattern" 2>/dev/null
done
sleep 1
echo "remaining=$(pgrep -c -f 'robot_collab|gzserver|slam_toolbox|static_transform_publisher' 2>/dev/null || echo 0)"
echo "stopped"
