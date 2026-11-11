#!/bin/bash
# Launch the Gazebo demo with a visible window (WSLg) plus the simulated node stack.
#
#   scripts/run_gazebo_demo.sh              # lab world, simulated nav backend
#   scripts/run_gazebo_demo.sh --nav2       # SLAM + Nav2
#   scripts/run_gazebo_demo.sh --workshop   # workshop world + real KUKA/FANUC/Dobot
#   scripts/run_gazebo_demo.sh --headless   # gzserver only, no window
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
# Gazebo at a small model directory instead of all of ROS share.
# Do NOT override GAZEBO_RESOURCE_PATH: doing so hides Gazebo's own Ogre media
# and the render engine fails to initialise.
GAZEBO_MODELS="$("$REPO/scripts/setup_gazebo_models.sh")"
export GAZEBO_MODEL_PATH="$GAZEBO_MODELS:${GAZEBO_MODEL_PATH:-}"

NAV_ARGS=()
WORLD_ARGS=()
SPAWN_ARMS=0
HEADLESS=0
for arg in "$@"; do
  case "$arg" in
    --nav2) NAV_ARGS=(nav_backend:=nav2 start_slam:=true start_nav2:=true) ;;
    --workshop)
      WORLD_ARGS=(world:="$REPO/src/robot_collab_sim/worlds/workshop.world")
      SPAWN_ARMS=1
      ;;
    --headless) HEADLESS=1 ;;
  esac
done

if [ "$HEADLESS" = "1" ]; then
  # No window: gzserver alone. The stack is still fully drivable, which is
  # what a machine with no display (or a CI runner) needs.
  GUI_ARGS=(gui:=false)
  echo "headless: gzserver only, no window"
else
  GUI_ARGS=()
fi

LOG=/tmp/gazebo_demo.log
: >"$LOG"
setsid nohup ros2 launch robot_collab_bringup gazebo_nav_vins_demo.launch.py \
  "${WORLD_ARGS[@]}" "${NAV_ARGS[@]}" "${GUI_ARGS[@]}" >"$LOG" 2>&1 &

echo "launch started; log: $LOG"
for i in $(seq 1 40); do
  if pgrep -f gzserver >/dev/null 2>&1; then
    echo "gazebo server up after ${i}s"
    break
  fi
  if [ "$HEADLESS" != "1" ] && pgrep -f gzclient >/dev/null 2>&1; then
    echo "gazebo window up after ${i}s"
    break
  fi
  sleep 1
done

if [ "$SPAWN_ARMS" = "1" ]; then
  for i in $(seq 1 40); do
    timeout 3 ros2 service list 2>/dev/null | grep -q "/spawn_entity" && break
    sleep 1
  done
  echo "spawning official arms"
  bash "$REPO/scripts/spawn_workshop_arms.sh" | tail -5
fi

echo "stop with: scripts/stop_gazebo_demo.sh"
