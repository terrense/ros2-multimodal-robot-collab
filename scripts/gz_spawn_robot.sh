#!/usr/bin/env bash
# Put the robot into a running gz-sim world.
#
#   scripts/gz_spawn_robot.sh [world_name]
#
# The lab world holds the furniture; the robot is spawned into it, the way
# the Gazebo Classic launch spawns it. gz-sim parses URDF, so the
# description is rendered with the gz-sim flavour and converted to SDF
# before it is handed to the world's create service.
#
# The diff-drive system that drives the wheels is a system of the world
# and lives in the world file, not here.
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

export PYTHONPATH="$REPO:${PYTHONPATH:-}"

XACRO="$REPO/install/robot_collab_sim/share/robot_collab_sim/urdf/collab_mobile_base.urdf.xacro"
URDF=/tmp/${NAME}_gz.urdf
SDF=/tmp/${NAME}_gz.sdf

echo "# rendering the description for gz-sim"
python3 -m robot_collab_sim.robot_description gz-sim "$XACRO" >"$URDF"

echo "# converting it to SDF"
ign sdf -p "$URDF" >"$SDF.raw"
# Fortress reads SDF 1.8; the converter writes the newest it knows.
sed -i "s/version='1.9'/version='1.8'/" "$SDF.raw"

# The diff-drive system drives the robot's two wheels. It is a plugin of
# the model -- a system of the world would be configured before the robot
# is spawned and would find no joints to drive -- and a URDF has no place
# for one, so it goes in here.
python3 - "$SDF.raw" "$SDF" <<'PYTHON'
import sys

DIFF_DRIVE = """    <plugin filename="ignition-gazebo6-diff-drive-system"
            name="ignition::gazebo::systems::DiffDrive">
      <topic>/diff_drive_controller/cmd_vel</topic>
      <odom_topic>/diff_drive_controller/odom</odom_topic>
      <left_joint>left_wheel_joint</left_joint>
      <right_joint>right_wheel_joint</right_joint>
      <wheel_separation>0.72</wheel_separation>
      <wheel_radius>0.06</wheel_radius>
      <frame_id>odom</frame_id>
      <odom_frame_id>odom</odom_frame_id>
      <child_frame_id>base_footprint</child_frame_id>
      <max_linear_acceleration>1.0</max_linear_acceleration>
      <max_angular_acceleration>2.0</max_angular_acceleration>
    </plugin>
"""

source, target = sys.argv[1], sys.argv[2]
text = open(source, encoding="utf-8").read()
if "<model" not in text:
    raise SystemExit("the converted SDF has no model in it")
# The plugin goes first in the model: the parser reads a model's children
# in order, and a plugin that comes after the links is one it does not
# get to.
marker = "<model name='collab_mobile_base'>"
index = text.index(marker) + len(marker)
text = text[:index] + "\n" + DIFF_DRIVE + text[index:]
open(target, "w", encoding="utf-8").write(text)
print("the diff-drive system is in the model")
PYTHON

echo "# waiting for the world"
for _ in $(seq 1 240); do
  if ign service -l 2>/dev/null | grep -q "/world/${WORLD}/create"; then
    break
  fi
  sleep 1
done

echo "# spawning ${NAME} into ${WORLD}"
ign service -s "/world/${WORLD}/create" \
  --reqtype ignition.msgs.EntityFactory \
  --reptype ignition.msgs.Boolean \
  --timeout 120000 \
  --req "name: \"${NAME}\" sdf_filename: \"${SDF}\" pose: {position: {x: 0, y: 0, z: 0.05}}"
