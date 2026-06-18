#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f /opt/ros/humble/setup.bash ]; then
  echo "ROS2 Humble is missing. Install it first." >&2
  exit 1
fi

if [ ! -f install/setup.bash ]; then
  echo "Workspace overlay missing. Run scripts/build_ros2_workspace.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source install/setup.bash

if [ "${SOFTWARE_RENDERING:-0}" = "1" ]; then
  export LIBGL_ALWAYS_SOFTWARE=1
fi

LAUNCH_ARGS=("$@")

# FULL_NAV=1 starts the complete visible navigation stack (SLAM + Nav2 + the
# nav2 station backend) unless the caller already passed those args.
if [ "${FULL_NAV:-0}" = "1" ] && [ "$#" -eq 0 ]; then
  LAUNCH_ARGS=(start_slam:=true start_nav2:=true nav_backend:=nav2)
  echo "FULL_NAV=1: starting SLAM + Nav2 + nav2 station backend."
fi

echo "Launching Gazebo visible robot demo ..."
echo "Launch args: ${LAUNCH_ARGS[*]:-<none>}"
ros2 launch robot_collab_bringup gazebo_nav_vins_demo.launch.py "${LAUNCH_ARGS[@]}"
