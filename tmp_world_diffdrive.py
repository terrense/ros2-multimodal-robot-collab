"""The diff-drive system belongs to the world, not to the model.

A model-level plugin in gz-sim is loaded for the model it sits in, and
the diff-drive system's joints live in the robot's model while the system
itself belongs to the world that contains it. Put it in the world's
plugin block instead, next to the sensors system.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[0]

# 1. The world generator: the diff-drive system joins the sensors system.
GEN = REPO / "scripts" / "make_gz_sim_world.py"
text = GEN.read_text(encoding="utf-8")
OLD = '''    <plugin filename="ignition-gazebo6-sensors-system"
            name="gz::sim::systems::Sensors">
    </plugin>"""'''
NEW = '''    <plugin filename="ignition-gazebo6-sensors-system"
            name="gz::sim::systems::Sensors">
    </plugin>
    <!-- The diff-drive system drives the robot's two wheels. It is a system
         of the world: the joints it commands are in the robot's model, and
         the system that commands them belongs to the world that holds
         the robot. -->
    <plugin filename="ignition-gazebo6-diff-drive-system"
            name="ignition::gazebo::systems::DiffDrive">
      <topic>/diff_drive_controller/cmd_vel</topic>
      <odom_topic>/diff_drive_controller/odom</odom_topic>
      <left_joint>left_wheel_joint</left_joint>
      <right_joint>right_wheel_joint</right_joint>
      <wheel_separation>0.72</wheel_separation>
      <wheel_radius>0.06</wheel_radius>
      <frame_id>odom</frame_id>
      <odom_frame_id>odom</odom_frame_id>
      <child_frame_id>base_footprint</child_frame_id>
      <max_linear_acceleration>1.0</max_linear_acceleration>
      <max_angular_acceleration>2.0</max_angular_acceleration>
    </plugin>"""'''
if OLD not in text:
    raise SystemExit("the sensors system is not as written")
GEN.write_text(text.replace(OLD, NEW), encoding="utf-8")
print("the diff-drive system is a system of the world")

# 2. The spawn script: it no longer adds the plugin itself.
SPAWN = REPO / "scripts" / "gz_spawn_robot.sh"
text = SPAWN.read_text(encoding="utf-8")
start = text.index("# gz-sim's diff-drive system drives the two wheel joints.")
end = text.index("PYTHON", start) + len("PYTHON")
text = text[:start] + text[end:]
text = text.replace('ign sdf -p "$URDF" >"$SDF.raw"', 'ign sdf -p "$URDF" >"$SDF"')
SPAWN.write_text(text, encoding="utf-8")
print("the spawn script leaves the plugin to the world")
