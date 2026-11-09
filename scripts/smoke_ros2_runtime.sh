#!/bin/bash
# One-command ROS 2 smoke test for the tool runtime.
#
# Requires: Ubuntu 22.04 + ROS 2 Humble, and a built workspace:
#   source /opt/ros/humble/setup.bash
#   colcon build --base-paths src
#
# What it does: launches the simulated node stack, waits for the mission
# action server, runs one delivery plan through the runtime engine with a
# temporary ledger, then prints the ledger rows (including the ROS goal uuid).
#
# It drives simulated backends only. That is not hardware validation.
set -o pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEDGER="${1:-/tmp/ros2_runtime_smoke.db}"
LOG=/tmp/ros2_runtime_smoke_demo.log
cd "$REPO" || exit 1

source /opt/ros/humble/setup.bash
source install/setup.bash
# Prepend the repo so `agent_harness` imports, without dropping ROS's own path.
export PYTHONPATH="$REPO:${PYTHONPATH:-}"

rm -f "$LEDGER"
: >"$LOG"

setsid ros2 launch robot_collab_bringup demo_sim.launch.py >"$LOG" 2>&1 &
LAUNCH_PID=$!
cleanup() {
  kill -INT -- -"$LAUNCH_PID" 2>/dev/null
  sleep 2
  kill -9 -- -"$LAUNCH_PID" 2>/dev/null
}
trap cleanup EXIT

for _ in $(seq 1 25); do
  timeout 10 ros2 action list 2>/dev/null | grep -q "/mission/deliver_tool" && break
  sleep 1
done

echo "== query_state =="
timeout 15 ros2 service call /system/query_state \
  robot_collab_interfaces/srv/QuerySystemState "{requester: 'smoke_test'}" 2>&1 | tail -3

echo "== execute delivery plan (runtime engine, ledger: $LEDGER) =="
timeout 90 python3 agent_harness/scripts/ros_tool_executor.py \
  agent_harness/examples/deliver_hex_key_plan.json \
  --mode execute --engine runtime --timeout-sec 30 --ledger "$LEDGER"
EXIT=$?
echo "harness_exit=$EXIT"

echo "== ledger =="
python3 - "$LEDGER" <<'PY'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
rows = conn.execute(
    "SELECT capability, status, goal_uuid, failure_code FROM commands ORDER BY created_at"
).fetchall()
for capability, status, goal_uuid, failure in rows:
    print(f"{capability}: {status} goal_uuid={goal_uuid} failure={failure}")
print(f"commands={len(rows)}")
PY
exit "$EXIT"
