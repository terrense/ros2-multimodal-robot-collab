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

GZ_PLUGINS = """    <!-- gz-sim publishes the world's state itself; the Classic state plugin
         has no counterpart here. The ROS side gets the model states from
         the bridged world-state topic.

         The physics system is what moves anything. A world without it
         loads, spawns the robot, publishes the camera's frames and does
         nothing at all: the wheels turn in the diff drive's own numbers
         and the chassis stands still. The physics engine itself is chosen
         by the <physics> element below, not by this plugin.

         The sensors system is what renders the cameras and the lidars. In
         Gazebo Classic the camera plugin brought its own sensor with it;
         in gz-sim the sensor is a system of the world, and a world without
         it advertises the camera's topics and never publishes a frame. -->
    <plugin filename="ignition-gazebo6-physics-system"
            name="ignition::gazebo::systems::Physics">
    </plugin>
    <plugin filename="ignition-gazebo6-sensors-system"
            name="gz::sim::systems::Sensors">
    </plugin>
    <!-- The user-commands system is what provides the world's create
         service: without it the robot cannot be spawned into the world at
         all. -->
    <plugin filename="ignition-gazebo6-user-commands-system"
            name="gz::sim::systems::UserCommands">
    </plugin>
    <!-- The scene broadcaster is what the window shows. A server without
         it simulates perfectly and a window connected to it shows nothing
         at all: there is no scene to draw. -->
    <plugin filename="ignition-gazebo6-scene-broadcaster-system"
            name="ignition::gazebo::systems::SceneBroadcaster">
    </plugin>"""

#: The name the lab generator used before the workshop one joined it.
GZ_STATE_PLUGIN = GZ_PLUGINS

CLASSIC_PHYSICS = """    <physics name="default_physics" default="0" type="ode">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>1000</real_time_update_rate>
    </physics>"""

GZ_PHYSICS = """    <!-- The engine is bullet. gz-sim reads the physics element itself, and
         "ignored" is not a fast engine: it is the absence of one, and a
         world without an engine holds every model still while the camera
         renders it perfectly. -->
    <physics name="2ms" type="bullet">
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
    </plugin>
"""


POSE_PUBLISHER = """    <plugin filename="ignition-gazebo6-pose-publisher-system"
            name="ignition::gazebo::systems::PosePublisher">
      <!-- One message with every pose in it. Without this the publisher
           sends one pose per entity, one after the other, and a reader
           that wants the model and its links has to collect them itself. -->
      <use_pose_vector_msg>true</use_pose_vector_msg>
      <publish_model_pose>true</publish_model_pose>
      <publish_link_pose>true</publish_link_pose>
      <publish_nested_model_pose>true</publish_nested_model_pose>
      <publish_visual_pose>false</publish_visual_pose>
      <publish_collision_pose>false</publish_collision_pose>
      <publish_sensor_pose>false</publish_sensor_pose>
    </plugin>
"""


def home_pose() -> str:
    """Where the robot stands in the world: the home station's pose.

    The classic stack spawns the robot at the home station's heading, so
    that it starts looking at the tool shelf: a camera that starts
    pointing at a wall sees nothing, and the measurement that needs the
    robot to look at the tool would answer a question nobody asked. The
    registry is the same one the classic launch reads.
    """
    registry = (
        REPO_ROOT
        / "install"
        / "robot_collab_bringup"
        / "share"
        / "robot_collab_bringup"
        / "config"
        / "stations.yaml"
    )
    if not registry.exists():
        registry = (
            REPO_ROOT
            / "src"
            / "robot_collab_bringup"
            / "config"
            / "stations.yaml"
        )
    try:
        import yaml

        data = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - the world is generated either way
        data = {}
    pose = ((data.get("stations") or {}).get("home") or {}).get("pose") or {}
    return (
        f"{float(pose.get('x', 0.0))} {float(pose.get('y', 0.0))} 0 "
        f"0 0 {float(pose.get('yaw', 0.0))}"
    )


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
    model = model[:index] + "\n" + DIFF_DRIVE + POSE_PUBLISHER + model[index:]
    # And the robot stands where the classic stack spawns it: at the home
    # station, looking at the tool shelf.
    pose = f"    <pose>{home_pose()}</pose>\n"
    index = model.index(marker) + len(marker)
    return model[:index] + "\n" + pose + model[index:]


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
    # world file, so the floor gets an absolute path. It is the bullet
    # floor: the Classic one carries its contact parameters for ODE, and
    # gz-sim's engine does not read that element.
    floor = (
        REPO_ROOT
        / "install"
        / "robot_collab_sim"
        / "share"
        / "robot_collab_sim"
        / "models"
        / "ground_plane_gz"
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
