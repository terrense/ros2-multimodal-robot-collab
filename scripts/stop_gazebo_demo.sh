#!/bin/bash
# Stop everything started by scripts/run_gazebo_demo.sh.
#
# Nothing here matches a process by a generic name. The demo starts the
# launch with setsid, so the launch leads its own process group and every
# node it started belongs to that group: signalling the group takes the
# whole stack down, and nothing outside it is touched. The leftover sweep
# afterwards matches this repository's own install path.
#
# Signalling the launch alone is not enough -- the nodes keep running, and
# a session that restarts the demo a few times ends up with a dozen silent
# nodes eating memory.
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_PATTERN="ros2 launch robot_collab_bringup"

group_ids() {
  pgrep -f "$LAUNCH_PATTERN" 2>/dev/null \
    | xargs -r ps -o pgid= 2>/dev/null \
    | tr -d ' ' | tr -s ' ' '\n' | grep -E '^[0-9]+$' | sort -u
}

for pgid in $(group_ids); do
  kill -INT -- -"$pgid" 2>/dev/null
done
pkill -INT -f "$LAUNCH_PATTERN" 2>/dev/null
sleep 3

# Whatever the group signal missed: this repository's own node executables.
for pid in $(pgrep -f "$REPO/install/robot_collab_" 2>/dev/null); do
  kill -9 "$pid" 2>/dev/null
done
# And the simulator this demo started, identified by the world it was given.
for pid in $(pgrep -f "gzserver.*$REPO/install/robot_collab_sim" 2>/dev/null); do
  kill -9 "$pid" 2>/dev/null
done
sleep 1
echo "remaining=$(pgrep -c -f "$REPO/install/robot_collab_|gzserver.*$REPO" 2>/dev/null || echo 0)"
echo "stopped"
