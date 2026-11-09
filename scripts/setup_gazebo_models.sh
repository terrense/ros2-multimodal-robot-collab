#!/bin/bash
# Build a minimal Gazebo model path for the official sensor descriptions.
#
# Official xacro files reference model://velodyne_description/... and
# model://realsense2_description/... . Putting all of /opt/ros/humble/share on
# GAZEBO_MODEL_PATH makes Gazebo scan thousands of package directories before
# it initialises the ROS plugins, which stalls spawn and leaves the graph empty.
# Two symlinks are enough.
set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS="$REPO/gazebo_models"
mkdir -p "$MODELS"
for pkg in velodyne_description realsense2_description; do
  src="/opt/ros/humble/share/$pkg"
  if [ -d "$src" ]; then
    ln -sfn "$src" "$MODELS/$pkg"
  fi
done
echo "$MODELS"
