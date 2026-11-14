#!/usr/bin/env bash
# One sentence in, a robot that does it.
#
#   scripts/nl_mission.sh "take the hex key to station A"
#   scripts/nl_mission.sh "drive to the handover zone" --dry-run
#
# The sentence goes to a hosted model (DeepSeek or MiniMax), the model
# answers with a mission plan, the harness validates every step of that
# plan against the skills this robot actually has, and the plan is
# dispatched through the ROS 2 runtime.
#
# The API key comes from the environment (DEEPSEEK_API_KEY or
# MINIMAX_API_KEY) or from a local .env file. It is never kept in the
# repository, which is public.
#
# The Gazebo stack has to be up: scripts/run_gazebo_demo.sh
set -o pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

if [ $# -lt 1 ]; then
  echo 'usage: nl_mission.sh "what the robot should do" [--dry-run]' >&2
  exit 2
fi

PROMPT="$1"
shift

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

export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
export PYTHONPATH="$REPO:${PYTHONPATH:-}"

PLAN="agent_harness/generated/last_plan.json"
mkdir -p "$(dirname "$PLAN")"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
  esac
done

echo "== asking the model =="
python3 -m agent_harness.scripts.model_mission_planner "$PROMPT" --output "$PLAN" "$@" || exit 1

if [ "$DRY_RUN" = "1" ]; then
  echo "== dry run: nothing was dispatched =="
  python3 agent_harness/scripts/ros_tool_executor.py "$PLAN" --mode dry-run --json
  exit 0
fi

echo "== executing the plan against the live stack =="
python3 agent_harness/scripts/ros_tool_executor.py "$PLAN" --mode execute --timeout-sec 180
