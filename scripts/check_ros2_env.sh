#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=0

pass() {
  printf "[OK] %s\n" "$1"
}

warn() {
  printf "[WARN] %s\n" "$1"
}

fail() {
  printf "[FAIL] %s\n" "$1"
  FAILURES=$((FAILURES + 1))
}

check_cmd() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    pass "command found: $name -> $(command -v "$name")"
  else
    fail "missing command: $name"
  fi
}

printf "== System ==\n"
if [ -f /etc/os-release ]; then
  . /etc/os-release
  printf "Ubuntu: %s %s\n" "${VERSION_ID:-unknown}" "${VERSION_CODENAME:-unknown}"
  if [ "${VERSION_ID:-}" != "22.04" ]; then
    warn "ROS2 Humble is intended for Ubuntu 22.04."
  fi
else
  warn "/etc/os-release not found."
fi

printf "\n== ROS2 setup ==\n"
if [ -f /opt/ros/humble/setup.bash ]; then
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash
  pass "found /opt/ros/humble/setup.bash"
else
  fail "ROS2 Humble not found at /opt/ros/humble/setup.bash"
fi

check_cmd ros2
check_cmd colcon
check_cmd gazebo

printf "\n== ROS2 packages ==\n"
for pkg in gazebo_ros nav2_bringup slam_toolbox robot_localization xacro tf2_ros; do
  if ros2 pkg prefix "$pkg" >/dev/null 2>&1; then
    pass "ROS2 package found: $pkg"
  else
    fail "missing ROS2 package: $pkg"
  fi
done

printf "\n== Workspace ==\n"
printf "repo: %s\n" "$ROOT_DIR"
if [ -f "$ROOT_DIR/install/setup.bash" ]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/install/setup.bash"
  pass "workspace overlay found: install/setup.bash"
else
  warn "workspace is not built yet: install/setup.bash missing"
fi

for pkg in robot_collab_bringup robot_collab_sim robot_collab_agent robot_collab_core robot_collab_navigation; do
  if ros2 pkg prefix "$pkg" >/dev/null 2>&1; then
    pass "workspace package found: $pkg"
  else
    warn "workspace package not visible yet: $pkg"
  fi
done

printf "\n== Perception assets ==\n"
if [ -f "$ROOT_DIR/models/yolov8n.pt" ]; then
  pass "YOLOv8 weights present: models/yolov8n.pt"
else
  warn "models/yolov8n.pt missing (run scripts/fetch_models.sh; Ultralytics can also auto-download)"
fi
if [ -f "$ROOT_DIR/models/coco_sample.jpg" ]; then
  pass "sample image present: models/coco_sample.jpg"
else
  warn "models/coco_sample.jpg missing (run scripts/fetch_models.sh for static-image YOLO mode)"
fi
for mod in ultralytics cv2 numpy; do
  if python3 -c "import $mod" >/dev/null 2>&1; then
    pass "python module found: $mod"
  else
    warn "python module missing: $mod (pip install ultralytics opencv-python numpy)"
  fi
done

printf "\n== Result ==\n"
if [ "$FAILURES" -eq 0 ]; then
  pass "environment looks ready enough to build/run the demo"
  exit 0
fi

fail "$FAILURES blocking checks failed"
exit 1
