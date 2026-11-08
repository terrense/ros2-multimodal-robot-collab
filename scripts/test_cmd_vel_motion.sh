#!/usr/bin/env bash
set -o pipefail

source /opt/ros/humble/setup.bash
source /mnt/d/study_claudecode/ros2-multimodal-robot-collab-main/install/setup.bash

echo "---BEFORE-ODOM---"
timeout 6 ros2 topic echo /odom --once --field pose.pose.position

echo "---PUBLISHING-FOR-5s---"
timeout 5s ros2 topic pub --rate 10 /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.3}, angular: {z: 0.0}}"

echo "---AFTER-ODOM---"
timeout 6 ros2 topic echo /odom --once --field pose.pose.position

echo "---STOP---"
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 0.0}}"
echo TEST_DONE
