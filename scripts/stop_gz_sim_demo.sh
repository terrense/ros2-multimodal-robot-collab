#!/usr/bin/env bash
# Stop the gz-sim stack.
#
#   scripts/stop_gz_sim_demo.sh
#
# The launch leads its own process group, so the group is signalled and
# everything under it goes with it: the simulator, the bridge, the nodes
# and the spawn script. One gz-sim takes a gigabyte of memory, and five of
# them on a machine with eight will run it out.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

stopped=0

# The launch and everything it started.
for pid in $(ps -eo pid,args | grep 'gz_sim_demo.launch.py' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done

# A simulator started outside the launch.
for pid in $(ps -eo pid,args | grep 'ign gazebo' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done

sleep 2
echo "stopped ${stopped} gz-sim processes"
free -m | head -2
