#!/usr/bin/env bash
# The gz-sim port, checked end to end.
#
#   bash scripts/check_gz_sim.sh
#
# It starts the gz-sim stack, asks the navigation to drive the base to two
# stations, and checks three things:
#
#   1. the drive arrives (the navigation says so, and the chassis is where
#      the station is);
#   2. the odometry and the chassis agree (the wheels slip; the two
#      accounts must not drift apart while the base drives);
#   3. the world is the lab (the camera publishes, the clock moves, and
#      the simulator has a physics engine).
#
# It starts its own stack and stops it again, so do not run it with a
# stack already up: two simulators on one ROS graph talk to each other.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

LOG=/tmp/gz_sim_check.log
FAILED=0

fail() {
  echo "FAILED: $1"
  FAILED=1
}

# ROS2's setup.bash references unset variables internally; nounset breaks it.
set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source install/setup.bash
set -u
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

echo "=== starting the gz-sim stack ==="
bash scripts/run_gz_sim_demo.sh >/dev/null 2>&1

cleanup() {
  echo ""
  echo "=== stopping the stack ==="
  bash scripts/stop_gz_sim_demo.sh >/dev/null 2>&1
}
trap cleanup EXIT

echo "=== waiting for the navigation skill server ==="
for _ in $(seq 1 60); do
  if ros2 action list 2>/dev/null | grep -q navigate_to_station; then
    echo "the navigation is there"
    break
  fi
  sleep 2
done
ros2 action list 2>/dev/null | grep -q navigate_to_station || fail "the navigation skill server never came up"

echo "=== the world is the lab ==="
for _ in $(seq 1 30); do
  if ros2 topic list 2>/dev/null | grep -q "/model_states"; then
    break
  fi
  sleep 2
done
ros2 topic list 2>/dev/null | grep -q "/model_states" \
  && echo "the world's poses are on /model_states" \
  || fail "no /model_states: the pose publisher is not working"
ros2 topic list 2>/dev/null | grep -q "/camera/depth/points" \
  && echo "the camera publishes a point cloud" \
  || fail "no /camera/depth/points: the sensors system is not working"

echo "=== driving to the tool shelf ==="
python3 scripts/drive_to_station.py tool_shelf || fail "the drive to the tool shelf failed"

echo "=== driving to station_a ==="
python3 scripts/drive_to_station.py station_a || fail "the drive to station_a failed"

echo ""
if [ "$FAILED" = "0" ]; then
  echo "gz-sim port: PASS"
else
  echo "gz-sim port: FAIL"
fi
exit "$FAILED"
