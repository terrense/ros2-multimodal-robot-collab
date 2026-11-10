#!/usr/bin/env bash
# The whole local gate, in one command.
#
#   bash scripts/check_all.sh            # everything that does not need ROS
#   bash scripts/check_all.sh --ros      # also the tests that need ROS 2
#
# It runs exactly what CI runs, in the same order, so "green locally" and
# "green in CI" mean the same thing. The ROS part is skipped without a ROS
# install rather than failing: the CI job that has ROS runs it.
#
# What it checks:
#
#   1. ruff over everything
#   2. the main suite (agent_env + training)
#   3. the harness suite (runtime: contracts, scheduling, concurrency, faults)
#   4. the ROS-only suite, when there is a ROS install
#   5. docs/results.md still matches the committed artifacts
#   6. every shell script parses
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

WITH_ROS=0
[ "${1:-}" = "--ros" ] && WITH_ROS=1

FAILED=0

step() {
  echo ""
  echo "=== $1 ==="
}

fail() {
  echo "FAILED: $1"
  FAILED=1
}

step "ruff"
python3 -m ruff check . || fail "ruff"

step "main suite (agent_env + training)"
python3 -m pytest 2>&1 | tail -3 || fail "main suite"

step "harness suite (runtime)"
python3 -m pytest agent_harness/tests 2>&1 | tail -3 || fail "harness suite"

step "ROS suite"
if [ "$WITH_ROS" = "1" ]; then
  # ROS2's setup.bash references unset variables internally; nounset breaks it.
  set +u
  # shellcheck disable=SC1091
  source /opt/ros/humble/setup.bash || fail "no ROS 2 Humble"
  if [ -f install/setup.bash ]; then
    # shellcheck disable=SC1091
    source install/setup.bash
  fi
  set -u
  python3 -m pytest agent_harness/tests/test_concurrency_ros.py 2>&1 | tail -3 || fail "ROS suite"
else
  echo "skipped (pass --ros to run it)"
fi

step "published results table"
python3 -m training.report --verify docs/results.md || fail "results.md is stale"

step "shell scripts"
for script in scripts/*.sh training/scripts/*.sh; do
  [ -f "$script" ] || continue
  bash -n "$script" || fail "$script does not parse"
done
echo "all shell scripts parse"

echo ""
if [ "$FAILED" = "0" ]; then
  echo "gate: PASS"
else
  echo "gate: FAIL"
fi
exit "$FAILED"
