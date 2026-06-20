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

echo "Launching Gazebo visible robot demo ..."
echo "Extra launch args: $*"
ros2 launch robot_collab_bringup gazebo_nav_vins_demo.launch.py "$@"
