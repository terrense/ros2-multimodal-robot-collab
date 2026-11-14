#!/usr/bin/env bash
# Put the robot into a running gz-sim world.
#
# The lab world holds the furniture; the robot is spawned into it, the way
# the Gazebo Classic launch spawns it. gz-sim parses URDF, so the
# description is rendered with the gz-sim controller plugin and converted
# to SDF before it is handed to the world's create service.
#
#   scripts/gz_spawn_robot.sh [world_name]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORLD="${1:-collab_lab}"
NAME="collab_mobile_base"

# ROS2's setup.bash references unset variables internally; nounset breaks it.
set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source "$REPO/install/setup.bash"
set -u

XACRO="$REPO/install/robot_collab_sim/share/robot_collab_sim/urdf/collab_mobile_base.urdf.xacro"
URDF=/tmp/${NAME}_gz.urdf
SDF=/tmp/${NAME}_gz.sdf

echo "# rendering the description for gz-sim"
python3 -m robot_collab_sim.robot_description gz-sim "$XACRO" >"$URDF"

echo "# converting it to SDF"
ign sdf -p "$URDF" >"$SDF"

echo "# waiting for the world"
for _ in $(seq 1 90); do
  if ign service -l 2>/dev/null | grep -q "/world/${WORLD}/create"; then
    break
  fi
  sleep 1
done

echo "# spawning ${NAME} into ${WORLD}"
ign service -s "/world/${WORLD}/create" \
  --reqtype ignition.msgs.EntityFactory \
  --reptype ignition.msgs.Boolean \
  --timeout 500 \
  --req "name: \"${NAME}\" sdf_filename: \"${SDF}\" pose: {position: {x: 0, y: 0, z: 0.05}}"
