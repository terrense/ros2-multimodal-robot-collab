#!/usr/bin/env python3
"""Write the gz-sim version of the lab world from the Gazebo Classic one.

The two simulators read the same world with three differences:

- the SDF version and the physics block (gz-sim does not use ODE's
  `default_physics`, it uses its own default and says so);
- the state publisher is a *system* in gz-sim, not a plugin;
- the rest of the models are the same, because the same robot drives
  around in both.

The Classic world is the source of truth. Editing the gz-sim world by
hand would let the two drift apart, and the whole point of the port is
that it is the same lab.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "src" / "robot_collab_sim" / "worlds" / "collab_lab.world"
TARGET = REPO_ROOT / "src" / "robot_collab_sim" / "worlds" / "collab_lab_gz.world"
XACRO = (
    REPO_ROOT
    / "install"
    / "robot_collab_sim"
    / "share"
    / "robot_collab_sim"
    / "urdf"
    / "collab_mobile_base.urdf.xacro"
)

CLASSIC_STATE_PLUGIN = """    <plugin name="gazebo_ros_state" filename="libgazebo_ros_state.so">
      <ros>
        <namespace>/</namespace>
      </ros>
      <update_rate>30.0</update_rate>
    </plugin>"""

GZ_STATE_PLUGIN = """    <!-- gz-sim publishes the world's state itself; the Classic state plugin
         has no counterpart here. The ROS side gets the model states from
         the bridged world-state topic.

         The sensors system is what renders the cameras and the lidars. In
         Gazebo Classic the camera plugin brought its own sensor with it;
         in gz-sim the sensor is a system of the world, and a world without
         it advertises the camera's topics and never publishes a frame. -->
    <plugin filename="ignition-gazebo6-sensors-system"
            name="gz::sim::systems::Sensors">
    </plugin>
    <!-- The user-commands system is what provides the world's create
         service: without it the robot cannot be spawned into the world at
         all. -->
    <plugin filename="ignition-gazebo6-user-commands-system"
            name="gz::sim::systems::UserCommands">
    </plugin>"""

CLASSIC_PHYSICS = """    <physics name="default_physics" default="0" type="ode">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>1000</real_time_update_rate>
    </physics>"""

GZ_PHYSICS = """    <physics name="2ms" type="ignored">
      <max_step_size>0.002</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>500</real_time_update_rate>
    </physics>"""


CLASSIC_SUN = """    <include>
      <uri>model://sun</uri>
    </include>"""

GZ_SUN = """    <!-- The sun, inline. gz-sim has no `model://sun` to find without the
         fuel models, and a workcell without a light is a workcell the
         cameras cannot see anything in. -->
    <light type="directional" name="sun">
      <cast_shadows>true</cast_shadows>
      <pose>0 0 10 0 0 0</pose>
      <diffuse>0.8 0.8 0.8 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>-0.5 0.1 -0.9</direction>
    </light>"""


DIFF_DRIVE = """    <plugin filename="ignition-gazebo6-diff-drive-system"
            name="ignition::gazebo::systems::DiffDrive">
      <topic>/world/collab_lab/model/collab_mobile_base/cmd_vel</topic>
      <odom_topic>/world/collab_lab/model/collab_mobile_base/odom</odom_topic>
      <left_joint>left_wheel_joint</left_joint>
      <right_joint>right_wheel_joint</right_joint>
      <wheel_separation>0.72</wheel_separation>
      <wheel_radius>0.06</wheel_radius>
      <frame_id>odom</frame_id>
      <odom_frame_id>odom</odom_frame_id>
      <child_frame_id>base_footprint</child_frame_id>
      <max_linear_acceleration>1.0</max_linear_acceleration>
      <max_angular_acceleration>2.0</max_angular_acceleration>
    </plugin>
"""


def robot_model() -> str:
    """The robot, as an SDF model, with the diff-drive system in it.

    The description is rendered for gz-sim and converted to SDF: gz-sim
    parses URDF, but the systems that drive the robot are named in SDF.
    """
    with tempfile.TemporaryDirectory() as directory:
        urdf_path = Path(directory) / "robot.urdf"
        rendered = subprocess.run(
            [
                sys.executable,
                "-m",
                "robot_collab_sim.robot_description",
                "gz-sim",
                str(XACRO),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        urdf_path.write_text(rendered.stdout, encoding="utf-8")
        converted = subprocess.run(
            ["ign", "sdf", "-p", str(urdf_path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    start = converted.index("<model")
    model = converted[start : converted.index("</model>") + len("</model>")]
    # Fortress reads SDF 1.8; the converter writes the newest it knows.
    model = model.replace("version='1.9'", "version='1.8'")
    # The plugin goes first in the model: the parser reads a model's children
    # in order, and a plugin that comes after the links is one it does not
    # get to.
    marker = "<model name='collab_mobile_base'>"
    index = model.index(marker) + len(marker)
    return model[:index] + "\n" + DIFF_DRIVE + model[index:]


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print("usage: make_gz_sim_world.py", file=sys.stderr)
        return 2
    text = SOURCE.read_text(encoding="utf-8")
    for old, new, what in (
        ('<sdf version="1.6">', '<sdf version="1.8">', "the SDF version"),
        (CLASSIC_PHYSICS, GZ_PHYSICS, "the physics block"),
        (CLASSIC_STATE_PLUGIN, GZ_STATE_PLUGIN, "the state publisher"),
        (CLASSIC_SUN, GZ_SUN, "the sun"),
    ):
        if old not in text:
            raise SystemExit(f"the lab world does not have {what} as written")
        text = text.replace(old, new)
    # gz-sim resolves a model URI against the resource path, not against the
    # world file, so the floor gets an absolute path.
    floor = (
        REPO_ROOT
        / "install"
        / "robot_collab_sim"
        / "share"
        / "robot_collab_sim"
        / "models"
        / "ground_plane"
    )
    text = text.replace("<uri>../models/ground_plane</uri>", f"<uri>{floor}</uri>")
    # The robot is in the world, so that the world's diff-drive system has
    # its joints when it is configured: a system of the world is configured
    # before anything is spawned into it.
    index = text.index("</world>")
    text = text[:index] + robot_model() + "\n" + text[index:]
    TARGET.write_text(text, encoding="utf-8")
    print(f"-> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
