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

# ROS2's setup.bash references unset variables internally; nounset breaks it.
set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source install/setup.bash
set -u

if [ "${SOFTWARE_RENDERING:-0}" = "1" ]; then
  export LIBGL_ALWAYS_SOFTWARE=1
fi

# The lab world only references locally-defined models; skip Gazebo Classic's
# online model database lookup so gzclient doesn't hang retrying network I/O.
export GAZEBO_MODEL_DATABASE_URI=""

# FULL_NAV=1 brings up the real navigation stack in one command: SLAM Toolbox
# (map->odom from the laser scan), Nav2 servers, and the nav2 backend in
# nav_skill_server so missions dispatch real NavigateToPose goals instead of
# the simulated progress loop. Without it the offline-runnable baseline (all
# simulated) is preserved.
EXTRA_ARGS=("$@")
if [ "${FULL_NAV:-0}" = "1" ]; then
  EXTRA_ARGS+=("nav_backend:=nav2" "start_slam:=true" "start_nav2:=true")
fi

echo "Launching Gazebo visible robot demo ..."
echo "FULL_NAV=${FULL_NAV:-0}"
echo "Launch args: ${EXTRA_ARGS[*]:-（none）}"
ros2 launch robot_collab_bringup gazebo_nav_vins_demo.launch.py ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}
