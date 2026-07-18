#!/usr/bin/env bash
# Launch N isolated simulation containers on one CPU host.
# Each container gets its own ROS_DOMAIN_ID and Gazebo master port so DDS
# discovery and gzserver instances never cross-talk.
#
# Usage: ./run_parallel_sim.sh <image> <num_instances> [command...]
# Example (demo smoke test, no Gazebo):
#   ./run_parallel_sim.sh robot-collab:latest 4 \
#     ros2 launch robot_collab_bringup demo_sim.launch.py
set -euo pipefail

IMAGE="${1:?image name required}"
COUNT="${2:?instance count required}"
shift 2
CMD=("$@")
[ ${#CMD[@]} -eq 0 ] && CMD=(ros2 launch robot_collab_bringup demo_sim.launch.py)

mkdir -p out
for i in $(seq 1 "$COUNT"); do
  docker run -d \
    --name "collab_sim_$i" \
    -e ROS_DOMAIN_ID="$i" \
    -e ROS_LOCALHOST_ONLY=1 \
    -e GAZEBO_MASTER_URI="http://127.0.0.1:$((11345 + i))" \
    -v "$(pwd)/out:/ws/out" \
    "$IMAGE" "${CMD[@]}"
  echo "started collab_sim_$i (ROS_DOMAIN_ID=$i)"
done
echo "logs:   docker logs -f collab_sim_1"
echo "stop:   docker rm -f \$(docker ps -aq --filter name=collab_sim_)"
