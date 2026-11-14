#!/usr/bin/env bash
# The stack in gz-sim (ros_gz) instead of Gazebo Classic.
#
#   scripts/run_gz_sim_demo.sh              # headless
#   scripts/run_gz_sim_demo.sh --gui        # with a window
#
# Needs the gz-sim packages:
#   sudo apt-get install -y --no-install-recommends ros-humble-ros-gz gz-fortress
#
# The world is the lab, generated from the Gazebo Classic one by
# scripts/make_gz_sim_world.py, and the robot is the same description with
# gz-sim's controller plugin.
set -o pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

if [ ! -f /opt/ros/humble/setup.bash ]; then
  echo "ROS2 Humble is missing. Install it first." >&2
  exit 1
fi
if [ ! -d /opt/ros/humble/share/ros_gz_sim ]; then
  echo "ros_gz_sim is missing. Install it:" >&2
  echo "  sudo apt-get install -y --no-install-recommends ros-humble-ros-gz gz-fortress" >&2
  exit 1
fi

# ROS2's setup.bash references unset variables internally; nounset breaks it.
set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source install/setup.bash
set -u

export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export PYTHONPATH="$REPO:${PYTHONPATH:-}"
export DISPLAY="${DISPLAY:-:0}"

# The world is generated: the classic lab world is the source of truth.
python3 scripts/make_gz_sim_world.py

LOG=/tmp/gz_sim_demo.log
: >"$LOG"
setsid nohup ros2 launch robot_collab_bringup gz_sim_demo.launch.py "$@" >"$LOG" 2>&1 &

echo "gz-sim launch started; log: $LOG"
for i in $(seq 1 60); do
  if pgrep -f gzserver >/dev/null 2>&1; then
    echo "gz sim up after ${i}s"
    break
  fi
  sleep 1
done

echo "stop with: pkill -f gz_sim_demo.launch.py"
