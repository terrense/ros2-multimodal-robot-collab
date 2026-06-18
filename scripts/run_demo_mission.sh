#!/usr/bin/env bash
# End-to-end mission driver against a live ROS2 graph.
#
#   - If MINIMAX_API_KEY is set (env or .env), generate the mission plan from the
#     natural-language prompt with the MiniMax planner.
#   - Otherwise fall back to the checked-in example plan so the chain still runs
#     without any API key.
#
# Then validate + dispatch the plan through the ROS2 harness (real Action/Service/
# Topic calls). Run this in a second terminal after launching the demo stack.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROMPT="${1:-deliver hex_key_3mm to station_a for operator_001}"
TIMEOUT_SEC="${TIMEOUT_SEC:-120}"
GENERATED_PLAN="agent_harness/generated/last_plan.json"
EXAMPLE_PLAN="agent_harness/examples/deliver_hex_key_plan.json"

if [ ! -f /opt/ros/humble/setup.bash ]; then
  echo "ROS2 Humble is missing. Install it first." >&2
  exit 1
fi
if [ ! -f install/setup.bash ]; then
  echo "Workspace overlay missing. Run scripts/build_ros2_workspace.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source /opt/ros/humble/setup.bash
# shellcheck disable=SC1091
source install/setup.bash

# Pull MINIMAX_API_KEY from .env if present so the check below sees it.
if [ -f .env ]; then
  # shellcheck disable=SC1091
  set -a; source .env; set +a
fi

PLAN_PATH="$EXAMPLE_PLAN"
if [ -n "${MINIMAX_API_KEY:-}" ]; then
  echo "MiniMax key found: generating plan from prompt:"
  echo "  \"$PROMPT\""
  if python3 agent_harness/scripts/minimax_mission_planner.py "$PROMPT" --output "$GENERATED_PLAN"; then
    PLAN_PATH="$GENERATED_PLAN"
  else
    echo "[WARN] MiniMax planning failed; falling back to checked-in plan." >&2
  fi
else
  echo "No MINIMAX_API_KEY; using checked-in plan: $EXAMPLE_PLAN"
fi

echo
echo "Dispatching plan through ROS2 harness: $PLAN_PATH"
python3 agent_harness/scripts/ros_tool_executor.py \
  "$PLAN_PATH" \
  --mode execute \
  --timeout-sec "$TIMEOUT_SEC"
