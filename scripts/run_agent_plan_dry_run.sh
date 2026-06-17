#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROMPT="${*:-deliver the 3mm hex key to station_a for operator_001}"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [ -n "${MINIMAX_API_KEY:-}" ]; then
  python3 agent_harness/scripts/minimax_mission_planner.py \
    "$PROMPT" \
    --output agent_harness/generated/last_plan.json \
    --dry-run
else
  echo "MINIMAX_API_KEY is not set; using checked-in example plan instead."
  python3 agent_harness/scripts/ros_tool_executor.py \
    agent_harness/examples/deliver_hex_key_plan.json
fi
