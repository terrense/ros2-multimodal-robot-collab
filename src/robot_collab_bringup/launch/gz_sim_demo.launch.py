"""The same stack, in gz-sim.

Gazebo Classic and gz-sim are two simulators with the same robots in
them. This launch is the classic one, with the simulator swapped: gz sim
instead of gazebo, a description that asks gz-sim for its controller
plugin, the robot spawned as a URDF (gz-sim parses URDF), and a bridge
that carries the depth camera's Gazebo Transport topics to the ROS topics
the rest of the stack uses.

Everything downstream of the simulator is the same nodes, the same
contract and the same calibration.

This is a first cut against the gz-sim (ros_gz) packages. The topic names
gz-sim gives the bridged sensors are the one thing here that was written
from the documentation rather than from a running simulator: check them
with `gz topic -l` once the packages are installed.
"""

import os
from pathlib import Path

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
)
from launch.substitutions import Command, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

#: The repository root, so the spawn script is found wherever the workspace is.
REPO_ROOT = Path(__file__).resolve().parents[3]

PREFIX = (
    "/world/collab_lab/model/collab_mobile_base/link/base_footprint/sensor"
)

#: The Gazebo Transport topics gz-sim gives the robot's sensors, and the
#: ROS topics the rest of the stack expects. The camera is the one that
#: matters: the detector reads its point cloud.
#: (gazebo topic, ros topic, gazebo type, ros type). The point cloud is not
#: the same message on the two sides: gz-sim publishes a packed cloud and
#: the stack expects the unpacked one, so the bridge converts it.
#: The Gazebo Transport topics gz-sim gives the robot's sensors, and the
#: ROS topics the rest of the stack uses. gz-sim scopes a sensor's topic by
#: the whole chain it hangs from -- world, model, link, sensor -- so these
#: names are long, and they are the ones the simulator prints.
#: The Gazebo Transport topics gz-sim gives the robot's sensors, and the
#: ROS topics the rest of the stack uses. gz-sim scopes a sensor's topic by
#: the whole chain it hangs from -- world, model, link, sensor -- so these
#: names are long, and they are the ones the simulator prints.
#:
#: The bridge takes `topic@ROS_type@Ignition_type`: the ROS type first,
#: then the Gazebo Transport one.
BRIDGES = (
    f"{PREFIX}/front_rgbd_camera/image@sensor_msgs/msg/Image@ignition.msgs.Image",
    f"{PREFIX}/front_rgbd_camera/depth_image@sensor_msgs/msg/Image@ignition.msgs.Image",
    f"{PREFIX}/front_rgbd_camera/points@sensor_msgs/msg/PointCloud2@ignition.msgs.PointCloudPacked",
    f"{PREFIX}/front_rgbd_camera/camera_info@sensor_msgs/msg/CameraInfo@ignition.msgs.CameraInfo",
    f"{PREFIX}/base_imu/imu@sensor_msgs/msg/Imu@ignition.msgs.IMU",
    "/clock@rosgraph_msgs/msg/Clock@ignition.msgs.Clock",
    # The base: gz-sim's diff-drive system takes the command on one topic
    # and answers with its own odometry on another. The navigation keeps
    # the names it knows; these two bridges are the whole difference.
)

#: The base: gz-sim's diff-drive system takes the command on one topic
#: and answers with its own odometry on another. The navigation keeps the
#: topic names it knows, and these two bridges are the whole difference
#: between the two simulators.
#:
#: `bridge_node` rather than `parameter_bridge`: the parameter one carries
#: one topic name for both sides, and the command has only one side with
#: messages on it (the Gazebo side is a subscription), so the direction
#: from Gazebo to ROS cannot be created and the bridge fails as a whole.
BASE_BRIDGE_COMMAND = (
    "/world/collab_lab/model/collab_mobile_base/cmd_vel@sensor_msgs/msg/Twist]ignition.msgs.Twist",
)

BASE_BRIDGE_ODOM = (
    "/world/collab_lab/model/collab_mobile_base/odom@nav_msgs/msg/Odometry[ignition.msgs.Odometry",
)


def generate_launch_description() -> LaunchDescription:
    use_sim_time = LaunchConfiguration("use_sim_time")

    sim_share = FindPackageShare("robot_collab_sim")
    bringup_share = FindPackageShare("robot_collab_bringup")
    xacro_path = PathJoinSubstitution(
        [sim_share, "urdf", "collab_mobile_base.urdf.xacro"]
    )
    world_path = PathJoinSubstitution(
        [sim_share, "worlds", "collab_lab_gz.world"]
    )
    params = PathJoinSubstitution([bringup_share, "config", "demo_params.yaml"])

    # The description gz-sim is given: the same robot, asking for gz-sim's
    # controller plugin and its native depth camera.
    robot_description = ParameterValue(
        Command(
            [
                "python3 -m robot_collab_sim.robot_description gz-sim ",
                xacro_path,
            ]
        ),
        value_type=str,
    )

    # The simulator, started here rather than through ros_gz's launch: that
    # one takes its arguments as a single string and performs it, and a
    # world path made of substitutions comes out as the text of the
    # substitution object.
    gz_sim = ExecuteProcess(
        cmd=[
            "ruby",
            "/usr/bin/ign",
            "gazebo",
            "-s",
            "-r",
            "-v",
            "4",
            world_path,
            "--force-version",
            "6",
        ],
        output="screen",
        additional_env={
            # gz-sim looks for the ogre2 render engine here, and the Ubuntu
            # package does not set it. Without it the sensors system waits
            # for an engine that never arrives and the world never finishes
            # initializing.
            "GZ_SIM_RENDER_ENGINE_PATH": os.environ.get(
                "GZ_SIM_RENDER_ENGINE_PATH", "/opt/gz-render-engine"
            ),
            "IGN_GAZEBO_RENDER_ENGINE_PATH": os.environ.get(
                "IGN_GAZEBO_RENDER_ENGINE_PATH", "/opt/gz-render-engine"
            ),
            "LIBGL_ALWAYS_SOFTWARE": os.environ.get(
                "LIBGL_ALWAYS_SOFTWARE", "1"
            ),
            # The robot description refers to its meshes as
            # model://velodyne_description/..., and the simulator has to be
            # told where the models are. A mesh it cannot find is a null
            # mesh, and a null mesh in the render engine takes the whole
            # simulator down.
            "GZ_SIM_RESOURCE_PATH": os.environ.get(
                "GZ_SIM_RESOURCE_PATH", "/opt/ros/humble/share"
            ),
            "GZ_SIM_MODEL_PATH": os.environ.get(
                "GZ_SIM_MODEL_PATH", "/opt/ros/humble/share"
            ),
        },
    )

    bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="gz_sensor_bridge",
        output="screen",
        parameters=[
            {
                "bridge.all": "false",
                "bridge.clock": "true",
            }
        ],
        arguments=list(BRIDGES),
        remappings=[],
    )

    base_command_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="gz_base_command_bridge",
        output="screen",
        arguments=list(BASE_BRIDGE_COMMAND),
    )

    base_odom_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="gz_base_odom_bridge",
        output="screen",
        arguments=list(BASE_BRIDGE_ODOM),
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument(
                "gui",
                default_value="false",
                description=(
                    "A window. The stack runs headless; a window is what a "
                    "person asks for on purpose."
                ),
            ),
            DeclareLaunchArgument("robot_name", default_value="collab_mobile_base"),
            gz_sim,
            Node(
                package="robot_state_publisher",
                executable="robot_state_publisher",
                name="robot_state_publisher",
                output="screen",
                parameters=[
                    {"use_sim_time": use_sim_time},
                    {"robot_description": robot_description},
                ],
            ),
            # gz-sim's controller manager lives in the server process, and
            # the spawner is what hands the controllers to it.
            ExecuteProcess(
                cmd=[
                    "bash",
                    "-c",
                    "until ros2 service list | grep -q /controller_manager; do "
                    "sleep 2; done; "
                    "ros2 run gz_ros2_control gz_ros2_control "
                    "--spawner joint_state_broadcaster "
                    "--controller-manager /controller_manager",
                ],
                output="screen",
            ),
            ExecuteProcess(
                cmd=[
                    "bash",
                    "-c",
                    "until ros2 service list | grep -q /controller_manager; do "
                    "sleep 2; done; "
                    "ros2 run gz_ros2_control gz_ros2_control "
                    "--spawner diff_drive_controller "
                    "--controller-manager /controller_manager",
                ],
                output="screen",
            ),
            bridge,
            base_command_bridge,
            base_odom_bridge,
            # The robot is in the world file: the world's diff-drive system
            # is configured before anything is spawned into the world, and
            # the joints it commands have to be there already.
            Node(
                package="robot_collab_perception",
                executable="tool_detector_node",
                name="tool_detector_node",
                output="screen",
                parameters=[
                    params,
                    {
                        "use_sim_time": use_sim_time,
                        "source_frame": "camera_link",
                        "tool_position": "1.25 -0.75 0.41",
                    },
                ],
            ),
            Node(
                package="robot_collab_navigation",
                executable="nav_skill_server",
                name="nav_skill_server",
                output="screen",
                parameters=[
                    params,
                    {
                        "use_sim_time": use_sim_time,
                        "cmd_vel_topic": "/world/collab_lab/model/collab_mobile_base/cmd_vel",
                        "odom_topic": "/world/collab_lab/model/collab_mobile_base/odom",
                    },
                ],
            ),
            Node(
                package="robot_collab_manipulation",
                executable="arm_skill_server",
                name="arm_skill_server",
                output="screen",
                parameters=[
                    params,
                    {"use_sim_time": use_sim_time},
                ],
            ),
            Node(
                package="robot_collab_core",
                executable="mission_state_machine",
                name="mission_state_machine",
                output="screen",
                parameters=[
                    params,
                    {"use_sim_time": use_sim_time},
                ],
            ),
        ]
    )
