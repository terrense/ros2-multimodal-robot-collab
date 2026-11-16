#!/usr/bin/env bash
# Stop the gz-sim stack.
#
#   scripts/stop_gz_sim_demo.sh
#
# The launch leads its own process group, so the group is signalled and
# everything under it goes with it: the simulator, the bridge, the nodes
# and the spawn script. One gz-sim takes a gigabyte of memory, and five of
# them on a machine with eight will run it out.
#
# The bridges are named and stopped by name as well: they survive the
# launch being stopped, and a bridge carrying a point cloud holds what it
# has not been able to deliver. Three of them, left over from three runs,
# held six gigabytes between them and left the simulator nothing to run
# on -- which looks exactly like a simulator that has hung.
set -uo pipefail

stopped=0

for pid in $(ps -eo pid,args | grep 'gz_sim_demo.launch.py' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done

for pid in $(ps -eo pid,args | grep 'ign gazebo' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done

for pid in $(ps -eo pid,args | grep 'ros_gz_bridge' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done

sleep 2
echo "stopped ${stopped} gz-sim processes"
free -m | head -2
