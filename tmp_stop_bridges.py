"""The stop script has to take the bridges with it.

A bridge that carries a point cloud holds what it has not been able to
deliver: three of them, left over from three runs, held six gigabytes
between them, and the machine had nothing left for the simulator. They
survive the launch being stopped, so they have to be named and stopped
by name.
"""

from pathlib import Path

STOP = Path(__file__).resolve().parents[0] / "scripts" / "stop_gz_sim_demo.sh"
text = STOP.read_text(encoding="utf-8")
OLD = """# A simulator started outside the launch.
for pid in $(ps -eo pid,args | grep 'ign gazebo' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done"""
NEW = """# A simulator started outside the launch.
for pid in $(ps -eo pid,args | grep 'ign gazebo' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done

# The bridges. They survive the launch, and a bridge holding a point cloud
# holds what it has not delivered: three of them, left over from three runs,
# took six gigabytes between them and left the simulator nothing to run on.
for pid in $(ps -eo pid,args | grep 'ros_gz_bridge' | grep -v grep | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && stopped=$((stopped + 1))
done"""
if OLD not in text:
    raise SystemExit("the stop script is not as written")
STOP.write_text(text.replace(OLD, NEW), encoding="utf-8")
print("the stop script takes the bridges with it")
