#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f /opt/ros/humble/setup.bash ]; then
  echo "ROS2 Humble is missing. Install it first, then rerun this script." >&2
  exit 1
fi

# ROS2's setup.bash references unset variables internally; nounset breaks it.
set +u
# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
set -u

echo "Installing ROS dependencies from src/ ..."
rosdep install --from-paths src -y --ignore-src

echo "Building ROS2 workspace from src/ only ..."
colcon build --symlink-install --base-paths src

echo
echo "Build complete. Source the overlay before running demos:"
echo "  source install/setup.bash"
