"""The robot description, in a form the controller manager can carry.

`gazebo_ros2_control` creates the controller manager inside the gzserver
process and hands it the robot description as a **command-line parameter
override**. rcl parses the value of such an override as YAML, and a YAML
plain scalar cannot contain a colon followed by a space: `note: here` is a
mapping, not a string. A URDF is full of colons -- in `package://` paths, in
`<remapping>` rules, and in the comments that document all of it -- and one
bare `: ` is enough for rcl to refuse the whole description.

The refusal is quiet from the outside. The plugin logs one line and carries
on without a controller manager, so the spawners wait forever for a service
that is never going to answer, and the robot's wheels never turn. The symptom
points at the controller manager; the cause is a colon in a comment.

So the description that reaches the nodes has no XML comments. They are
documentation, they are the only part of this robot's description that carries
a bare `: `, and nothing downstream reads them.

`render` fails loudly when a description still cannot be carried, because the
alternative -- a description that is quietly refused -- is a robot that
silently does not move.
"""

from __future__ import annotations

import math
import re
import subprocess
import sys
from xml.etree import ElementTree

#: An XML comment, including anything spanning several lines.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

#: A colon followed by whitespace: the sequence a YAML plain scalar cannot
#: hold. A colon followed by anything else (`package://`, `a:=b`) is fine.
_UNSAFE = re.compile(r":(?=\s)|:\s*$")


def strip_comments(text: str) -> str:
    """Remove the XML comments, and nothing else.

    The rest of the description is left byte for byte alone: the controller
    manager parses it as URDF, and a description that differs from the one the
    robot was spawned with is a description nobody can debug.
    """
    return _COMMENT.sub("", text)


def unsafe_spots(text: str) -> list[str]:
    """The places where a YAML plain scalar would break.

    Each spot is reported as ``line: the text`` so that the person editing
    the URDF is told where to look instead of being told that "rcl" said no.
    """
    spots: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _UNSAFE.finditer(line):
            start = max(0, match.start() - 30)
            spots.append(f"line {number}: ...{line[start:match.end() + 30].strip()}...")
    return spots


def carries_as_parameter(text: str) -> bool:
    """Whether rcl can carry this description as a parameter override.

    The check is the same one rcl makes, without needing rcl: a YAML plain
    scalar cannot hold a colon followed by whitespace.
    """
    return not unsafe_spots(text)


def render(xacro_path: str) -> str:
    """The robot description, ready to be handed to a node.

    The XML comments come out; everything else is exactly what xacro wrote.
    """
    result = subprocess.run(
        ["xacro", xacro_path],
        check=True,
        capture_output=True,
        text=True,
    )
    description = strip_comments(result.stdout)
    spots = unsafe_spots(description)
    if spots:
        raise SystemExit(
            "the robot description cannot be carried as a parameter override: "
            "a YAML plain scalar cannot hold a colon followed by a space.\n"
            + "\n".join(f"  {spot}" for spot in spots)
            + "\nFix the description, or teach the controller manager to read it "
            "from the topic instead."
        )
    return description


def _rotation(rpy: tuple[float, float, float]) -> list[list[float]]:
    """A rotation matrix from fixed-axis roll, pitch and yaw."""
    roll, pitch, yaw = rpy
    cos_r, sin_r = math.cos(roll), math.sin(roll)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    return [
        [cos_y * cos_p, cos_y * sin_p * sin_r - sin_y * cos_r, cos_y * sin_p * cos_r + sin_y * sin_r],
        [sin_y * cos_p, sin_y * sin_p * sin_r + cos_y * cos_r, sin_y * sin_p * cos_r - cos_y * sin_r],
        [-sin_p, cos_p * sin_r, cos_p * cos_r],
    ]


def _multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [
        [sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3)]
        for row in range(3)
    ]


def _apply(rotation: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(  # type: ignore[return-value]
        sum(rotation[row][k] * vector[k] for k in range(3)) for row in range(3)
    )


def camera_mount(
    xacro_path: str,
    link: str = "camera_link",
    root: str = "base_footprint",
) -> tuple[float, float, float, float]:
    """Where the camera sits on the robot, read out of the description.

    The x, y, z of the camera in the robot's root frame, and the heading it
    has there. A node that has to know where the camera is -- a detector
    that looks at the world through it -- has to be told, and the
    description is where that is written down.

    The static transforms carry the same information, but they are
    published once, when the robot description arrives, and a subscriber
    that joins later does not always get them: the node then has no camera
    at all, and reports a tool it cannot see as a tool that is not there.
    """
    tree = ElementTree.fromstring(render(xacro_path))
    parents: dict[str, tuple[str, tuple[float, float, float], tuple[float, float, float]]] = {}
    for joint in tree.findall("joint"):
        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            continue
        origin = joint.find("origin")
        xyz = _floats(origin.get("xyz") if origin is not None else "0 0 0")
        rpy = _floats(origin.get("rpy") if origin is not None else "0 0 0")
        parents[str(child.get("link"))] = (str(parent.get("link")), xyz, rpy)

    chain: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    current = link
    while current != root:
        if current not in parents:
            raise SystemExit(f"the description has no joint leading to {current!r}")
        parent, xyz, rpy = parents[current]
        chain.append((xyz, rpy))
        current = parent
    chain.reverse()

    rotation = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    offset = (0.0, 0.0, 0.0)
    for xyz, rpy in chain:
        moved = _apply(rotation, xyz)
        offset = (
            offset[0] + moved[0],
            offset[1] + moved[1],
            offset[2] + moved[2],
        )
        rotation = _multiply(rotation, _rotation(rpy))
    yaw = math.atan2(rotation[1][0], rotation[0][0])
    return (offset[0], offset[1], offset[2], yaw)


def _floats(text: str) -> tuple[float, float, float]:
    parts = str(text).split()
    if len(parts) != 3:
        raise SystemExit(f"expected three numbers, got {text!r}")
    return (float(parts[0]), float(parts[1]), float(parts[2]))



#: The Gazebo Classic controller manager, as it is written in the
#: description. gz-sim does not use it: gz_ros2_control's controller
#: manager segfaults on ROS 2 Humble the moment it reads the hardware, and
#: the simulator goes down with it. gz-sim's own diff-drive system drives
#: the same two joints and needs no controller manager at all.
_CLASSIC_CONTROLLER = re.compile(
    r"\s*<gazebo>\s*<plugin[^>]*gazebo_ros2_control.*?</gazebo>",
    re.DOTALL,
)

_ROS2_CONTROL = re.compile(
    r"\s*<ros2_control[ >].*?</ros2_control>",
    re.DOTALL,
)


def _classic_controller_block(description: str) -> str:
    """The description without the Gazebo Classic controller manager.

    The `<ros2_control>` block goes with it: gz-sim's diff-drive system
    drives the wheels itself, and a hardware interface nobody claims is
    only a way for the simulator to disagree with the robot about what the
    wheels are doing.
    """
    description = _CLASSIC_CONTROLLER.sub("", description)
    return _ROS2_CONTROL.sub("", description)


def render_gz_sim(xacro_path: str) -> str:
    """The robot description, for gz-sim.

    gz-sim is a different simulator with a different controller plugin, and
    the description has to ask for the right one. The swap happens here
    rather than in the description because xacro evaluates a conditional on
    an argument before the argument is substituted, so a description that
    branches on a flag does not branch at all.

    The sensors are left alone: gz-sim parses the same `<sensor>` blocks as
    native sensors and ignores the ROS plugins inside them, so one
    description serves both simulators.
    """
    description = render(xacro_path)
    # gz_ros2_control's controller manager segfaults on ROS 2 Humble when
    # it reads the hardware, and the simulator goes down with it. gz-sim's
    # own diff-drive system drives the same two joints and needs no
    # controller manager, so the description leaves the controller to it.
    swapped = (
        _classic_controller_block(description)
        .replace(
            "<plugin>gazebo_ros2_control/GazeboSystem</plugin>",
            "<plugin>gz_ros2_control/GazeboSimSystem</plugin>",
        )
        # The official Velodyne description carries a collision with a
        # zero-radius sphere. Gazebo Classic ignores a collision it cannot
        # make, and gz-sim's physics asserts on it and takes the whole
        # simulator down with it. The housing is a cylinder of about that
        # size, so that is what it becomes.
        .replace('<sphere radius="0"/>', '<sphere radius="0.05"/>')
        # The depth camera: Gazebo Classic's camera plugin reads a
        # <sensor type="depth">, and gz-sim's own camera is an rgbd_camera
        # with a point cloud of its own. The detector reads that cloud.
        .replace(
            '<sensor name="front_rgbd_camera" type="depth">',
            '<sensor name="front_rgbd_camera" type="rgbd_camera">',
        )
        # gz-sim's rgbd_camera only gives a depth image and a point cloud
        # if the camera block says what the depth camera sees. Without it
        # the sensor is a plain camera, and the detector has no cloud to
        # read.
        # A cheaper camera. gz-sim renders with the software rasteriser
        # here, and at 15 Hz in 640x480 the simulation cannot keep up with
        # real time: the world never finishes a step, the state service
        # times out, and the diff-drive system never publishes.
        .replace(
            """      <always_on>true</always_on>
      <visualize>false</visualize>
      <update_rate>15</update_rate>""",
            """      <always_on>true</always_on>
      <visualize>false</visualize>
      <update_rate>5</update_rate>""",
        )
        .replace(
            """          <width>640</width>
          <height>480</height>""",
            """          <width>320</width>
          <height>240</height>""",
        )
        .replace(
            """        </clip>
      </camera>""",
            """        </clip>
        <depth_camera>
          <clip>
            <near>0.05</near>
            <far>8.0</far>
          </clip>
        </depth_camera>
      </camera>""",
        )
    )
    if swapped == description:
        raise SystemExit(
            "the description does not have the Gazebo Classic controller "
            "plugin in it, so it cannot be rendered for gz-sim."
        )
    if "gazebo_ros2_control" in swapped:
        raise SystemExit(
            "the description still names the Gazebo Classic controller "
            "plugin after the swap for gz-sim."
        )
    # The ROS sensor plugins are Gazebo Classic's: gz-sim has native
    # sensors and would try to load them as systems, fail, and say so on
    # every start. The sensor blocks themselves are what gz-sim reads.
    for name in (
        "front_rgbd_controller",
        "gazebo_ros_lidar",
        "gazebo_ros_laser_controller",
        "gazebo_ros_imu",
    ):
        swapped = _drop_plugin(swapped, name)
    return swapped


def _drop_plugin(description: str, name: str) -> str:
    """Remove one `<plugin>` block, by its name."""
    pattern = re.compile(
        rf"\s*<plugin [^>]*name=\"{re.escape(name)}\"[^>]*>.*?</plugin>", re.DOTALL
    )
    return pattern.sub("", description)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "mount":
        x, y, z, yaw = camera_mount(arguments[1])
        print(f"{x:.6f} {y:.6f} {z:.6f} {yaw:.6f}")
        return 0
    if len(arguments) == 2 and arguments[0] == "gz-sim":
        sys.stdout.write(render_gz_sim(arguments[1]))
        return 0
    if len(arguments) != 1:
        print(
            "usage: robot_description.py XACRO_PATH\n"
            "       robot_description.py mount XACRO_PATH\n"
            "       robot_description.py gz-sim XACRO_PATH",
            file=sys.stderr,
        )
        return 2
    sys.stdout.write(render(arguments[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
