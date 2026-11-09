#!/bin/bash
# Launch the Gazebo demo with a visible window (WSLg) plus the simulated node stack.
#
#   scripts/run_gazebo_demo.sh            # simulated nav backend
#   scripts/run_gazebo_demo.sh --nav2     # SLAM + Nav2 (experimental, see docs)
#
# Requirements: Ubuntu 22.04 + ROS 2 Humble, a built workspace, WSLg for the window.
# Leaves the stack running; stop it with scripts/stop_gazebo_demo.sh.
#
# On WSL with networkingMode=mirrored, Fast DDS loses cross-process topic data.
# CycloneDDS is required:  sudo apt install ros-humble-rmw-cyclonedds-cpp
set -o pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
source /opt/ros/humble/setup.bash
source install/setup.bash
export PYTHONPATH="$REPO:${PYTHONPATH:-}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export DISPLAY="${DISPLAY:-:0}"

# Official sensor descriptions are referenced through model:// URIs. Point
# Gazebo at a two-entry model directory instead of all of ROS share.
GAZEBO_MODELS="$("$REPO/scripts/setup_gazebo_models.sh")"
export GAZEBO_MODEL_PATH="$GAZEBO_MODELS:${GAZEBO_MODEL_PATH:-}"

NAV_ARGS=()
if [ "${1:-}" = "--nav2" ]; then
  NAV_ARGS=(nav_backend:=nav2 start_slam:=true start_nav2:=true)
fi

LOG=/tmp/gazebo_demo.log
: >"$LOG"
setsid nohup ros2 launch robot_collab_bringup gazebo_nav_vins_demo.launch.py \
  "${NAV_ARGS[@]}" >"$LOG" 2>&1 &

echo "launch started; log: $LOG"
for i in $(seq 1 40); do
  if pgrep -f gzclient >/dev/null 2>&1; then
    echo "gazebo window up after ${i}s"
    break
  fi
  sleep 1
done
echo "stop with: scripts/stop_gazebo_demo.sh"
