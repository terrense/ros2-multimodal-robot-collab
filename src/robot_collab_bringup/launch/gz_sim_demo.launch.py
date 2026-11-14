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

from pathlib import Path

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    IncludeLaunchDescription,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

#: The repository root, so the spawn script is found wherever the workspace is.
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The Gazebo Transport topics gz-sim gives the robot's sensors, and the
#: ROS topics the rest of the stack expects. The camera is the one that
#: matters: the detector reads its point cloud.
#: (gazebo topic, ros topic, gazebo type, ros type). The point cloud is not
#: the same message on the two sides: gz-sim publishes a packed cloud and
#: the stack expects the unpacked one, so the bridge converts it.
BRIDGES = (
    (
        "/collab_lab/collab_mobile_base/base_footprint/front_rgbd_camera/image",
        "/camera/color/image_raw",
        "ignition.msgs.Image",
        "sensor_msgs/msg/Image",
    ),
    (
        "/collab_lab/collab_mobile_base/base_footprint/front_rgbd_camera/depth_image",
        "/camera/depth/image_raw",
        "ignition.msgs.Image",
        "sensor_msgs/msg/Image",
    ),
    (
        "/collab_lab/collab_mobile_base/base_footprint/front_rgbd_camera/points",
        "/camera/depth/points",
        "ignition.msgs.PointCloudPacked",
        "sensor_msgs/msg/PointCloud2",
    ),
    (
        "/collab_lab/collab_mobile_base/base_footprint/front_rgbd_camera/camera_info",
        "/camera/color/camera_info",
        "ignition.msgs.CameraInfo",
        "sensor_msgs/msg/CameraInfo",
    ),
    (
        "/collab_lab/collab_mobile_base/base_footprint/base_imu/imu",
        "/imu/data",
        "ignition.msgs.IMU",
        "sensor_msgs/msg/Imu",
    ),
    ("/clock", "/clock", "ignition.msgs.Clock", "rosgraph_msgs/msg/Clock"),
)


def generate_launch_description() -> LaunchDescription:
    use_sim_time = LaunchConfiguration("use_sim_time")
    gui = LaunchConfiguration("gui")

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

    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution(
                [FindPackageShare("ros_gz_sim"), "launch", "gz_sim.launch.py"]
            )
        ),
        launch_arguments={
            "gz_args": f"-r -v 4 {world_path}" + (" -g" if gui else " -s"),
            "on_exit_shutdown": "true",
        }.items(),
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
        arguments=[item for group in BRIDGES for item in group],
        remappings=[],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument("gui", default_value="false"),
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
                    "ros2",
                    "run",
                    "gz_ros2_control",
                    "gz_ros2_control",
                    "--spawner",
                    "joint_state_broadcaster",
                    "--controller-manager",
                    "/controller_manager",
                ],
                output="screen",
            ),
            ExecuteProcess(
                cmd=[
                    "ros2",
                    "run",
                    "gz_ros2_control",
                    "gz_ros2_control",
                    "--spawner",
                    "diff_drive_controller",
                    "--controller-manager",
                    "/controller_manager",
                ],
                output="screen",
            ),
            bridge,
            # The robot is spawned into the world rather than loaded with
            # it: gz-sim parses the URDF and the controller manager comes up
            # with the robot.
            ExecuteProcess(
                cmd=["bash", str(REPO_ROOT / "scripts" / "gz_spawn_robot.sh")],
                output="screen",
                cwd=str(REPO_ROOT),
            ),
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
                        "cmd_vel_topic": "/diff_drive_controller/cmd_vel",
                        "odom_topic": "/diff_drive_controller/odom",
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
