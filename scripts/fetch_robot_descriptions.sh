#!/bin/bash
# Fetch the official robot descriptions used by the workshop.
# They are large (meshes), so they live in third_party/ and are gitignored.
set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$REPO/third_party/robot_descriptions"
mkdir -p "$DEST"

clone() {
  url="$1"; dir="$2"
  if [ -d "$DEST/$dir/.git" ]; then
    echo "$dir already present"
    return
  fi
  echo "cloning $dir"
  git clone --depth 1 "$url" "$DEST/$dir"
}

# Dobot official ROS 2 (Humble) package: CR10 / CR10A descriptions + meshes.
clone https://github.com/Dobot-Arm/DOBOT_6Axis_ROS2_V4.git dobot_6axis
# ROS-Industrial KUKA support: KR10 R1100 sixx description + meshes.
clone https://github.com/ros-industrial/kuka_experimental.git kuka_experimental

echo "done; descriptions in $DEST"
