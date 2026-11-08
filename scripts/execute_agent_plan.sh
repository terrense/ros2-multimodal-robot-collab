#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PLAN_PATH="${1:-agent_harness/generated/last_plan.json}"
TIMEOUT_SEC="${2:-120}"

if [ ! -f "$PLAN_PATH" ]; then
  echo "Plan not found: $PLAN_PATH"
  echo "Falling back to checked-in example plan."
  PLAN_PATH="agent_harness/examples/deliver_hex_key_plan.json"
fi

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

echo "Executing agent plan through ROS2 harness: $PLAN_PATH"
python3 agent_harness/scripts/ros_tool_executor.py \
  "$PLAN_PATH" \
  --mode execute \
  --timeout-sec "$TIMEOUT_SEC"
