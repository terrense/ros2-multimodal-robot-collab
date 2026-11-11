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
#
# SMOKE_USE_RUNNING=1 tests a stack that is already up (the workshop scene, for
# instance) instead of launching its own, and leaves it running afterwards.
#
# It talks to the graph with scripts/ros_graph.py rather than the `ros2` CLI:
# under WSL with CycloneDDS the CLI frequently cannot see a graph whose nodes
# are all running.
set -o pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEDGER="${1:-/tmp/ros2_runtime_smoke.db}"
LOG=/tmp/ros2_runtime_smoke_demo.log
cd "$REPO" || exit 1

source /opt/ros/humble/setup.bash
source install/setup.bash
# Prepend the repo so `agent_harness` imports, without dropping ROS's own path.
export PYTHONPATH="$REPO:${PYTHONPATH:-}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

rm -f "$LEDGER"
: >"$LOG"

LAUNCH_PID=""
cleanup() {
  [ -n "$LAUNCH_PID" ] || return 0
  kill -INT -- -"$LAUNCH_PID" 2>/dev/null
  sleep 2
  kill -9 -- -"$LAUNCH_PID" 2>/dev/null
}
trap cleanup EXIT

if [ "${SMOKE_USE_RUNNING:-0}" = "1" ]; then
  echo "using the stack that is already running"
else
  setsid ros2 launch robot_collab_bringup demo_sim.launch.py >"$LOG" 2>&1 &
  LAUNCH_PID=$!
fi

for _ in $(seq 1 25); do
  timeout 10 python3 scripts/ros_graph.py wait /mission/deliver_tool 5 >/dev/null 2>&1 && break
  sleep 1
done

echo "== query_state =="
timeout 20 python3 scripts/ros_graph.py call /system/query_state \
  robot_collab_interfaces/srv/QuerySystemState '{"requester": "smoke_test"}' 2>&1 | tail -3

echo "== execute delivery plan (runtime engine, ledger: $LEDGER) =="
timeout 90 python3 agent_harness/scripts/ros_tool_executor.py \
  agent_harness/examples/deliver_hex_key_plan.json \
  --mode execute --timeout-sec 30 --ledger "$LEDGER"
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
