#!/bin/bash
# Source the ROS 2 base and the built workspace overlay, then run the command.
#
# This is what makes the image usable without ceremony:
#
#   docker run --rm IMAGE ros2 pkg list          # sees robot_collab_*
#
# The base image's own entrypoint only sources /opt/ros/<distro>, so the
# workspace overlay has to be layered on top here.
set -e

source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"

if [ -f /ws/install/setup.bash ]; then
    source /ws/install/setup.bash
fi

exec "$@"
