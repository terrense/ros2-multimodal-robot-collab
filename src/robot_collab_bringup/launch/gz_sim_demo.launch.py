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
from launch.conditions import IfCondition
from launch.substitutions import (
    Command,
    LaunchConfiguration,
    PathJoinSubstitution,
    PythonExpression,
)
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
#:
#: gz-sim scopes a sensor's topic by the whole chain it hangs from --
#: world, model, link, sensor -- so these names are long, and they are the
#: ones the simulator prints. The ROS side keeps the names the rest of the
#: stack knows, and the remappings below are what does that.
#:
#: The bridge takes `topic@ROS_type[GZ_type`: the ROS type first, then the
#: Gazebo Transport one, and the bracket says which way the messages go.
#: One way only: a bridge that carries messages both ways publishes on the
#: Gazebo topic as well, and a second publisher on the clock's topic is
#: enough to make the simulator move its clock to another topic, where
#: nobody is listening.
BRIDGES = (
    f"{PREFIX}/front_rgbd_camera/image@sensor_msgs/msg/Image[ignition.msgs.Image",
    f"{PREFIX}/front_rgbd_camera/depth_image@sensor_msgs/msg/Image[ignition.msgs.Image",
    f"{PREFIX}/front_rgbd_camera/points@sensor_msgs/msg/PointCloud2[ignition.msgs.PointCloudPacked",
    f"{PREFIX}/front_rgbd_camera/camera_info@sensor_msgs/msg/CameraInfo[ignition.msgs.CameraInfo",
    f"{PREFIX}/base_imu/imu@sensor_msgs/msg/Imu[ignition.msgs.IMU",
    "/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock",
)

#: The ROS side of the same bridges: the names the rest of the stack uses,
#: so that the same nodes work in either simulator.
REMAPPINGS = [
    (f"{PREFIX}/front_rgbd_camera/image", "/camera/color/image_raw"),
    (f"{PREFIX}/front_rgbd_camera/depth_image", "/camera/depth/image_raw"),
    (f"{PREFIX}/front_rgbd_camera/points", "/camera/depth/points"),
    (f"{PREFIX}/front_rgbd_camera/camera_info", "/camera/color/camera_info"),
    (f"{PREFIX}/base_imu/imu", "/imu/data"),
]

#: The base: gz-sim's diff-drive system takes the command on one topic
#: and answers with its own odometry on another. The navigation keeps the
#: topic names it knows, and these two bridges are the whole difference
#: between the two simulators.
#:
#: `bridge_node` rather than `parameter_bridge`: the parameter one carries
#: one topic name for both sides, and the command has only one side with
#: messages on it (the Gazebo side is a subscription), so the direction
#: from Gazebo to ROS cannot be created and the bridge fails as a whole.


BASE_BRIDGE_ODOM = (
    "/diff_drive_controller/odom@nav_msgs/msg/Odometry[ignition.msgs.Odometry",
)


def generate_launch_description() -> LaunchDescription:
    use_sim_time = LaunchConfiguration("use_sim_time")

    sim_share = FindPackageShare("robot_collab_sim")
    bringup_share = FindPackageShare("robot_collab_bringup")
    xacro_path = PathJoinSubstitution(
        [sim_share, "urdf", "collab_mobile_base.urdf.xacro"]
    )
    gui = LaunchConfiguration("gui")
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
    #
    # The server runs headless, always. The window is a *separate* process:
    # gz-sim's `-g` means "the GUI only", and a window with no server
    # behind it is a black rectangle that asks for the list of worlds for
    # as long as it is open.
    gz_sim = ExecuteProcess(
        cmd=[
            "ruby",
            "/usr/bin/ign",
            "gazebo",
            "-r",
            "-v",
            "4",
            world_path,
            "-s",
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

    # The window. A process of its own: it connects to the server over the
    # Gazebo Transport and shows the world the server is running. Without a
    # window the stack runs the same way; the window is what a person asks
    # for on purpose.
    gz_gui = ExecuteProcess(
        cmd=["ruby", "/usr/bin/ign", "gazebo", "-g"],
        output="screen",
        # The window is restarted if it dies. WSLg's clipboard bridge takes
        # the GUI down when the Windows clipboard changes under it, and a
        # window that does not come back is a window the person has lost.
        respawn=True,
        respawn_delay=3.0,
        additional_env={
            "GZ_SIM_RENDER_ENGINE_PATH": os.environ.get(
                "GZ_SIM_RENDER_ENGINE_PATH", "/opt/gz-render-engine"
            ),
            "IGN_GAZEBO_RENDER_ENGINE_PATH": os.environ.get(
                "IGN_GAZEBO_RENDER_ENGINE_PATH", "/opt/gz-render-engine"
            ),
            "LIBGL_ALWAYS_SOFTWARE": os.environ.get(
                "LIBGL_ALWAYS_SOFTWARE", "1"
            ),
        },
        condition=IfCondition(
            PythonExpression(["'true' if '", gui, "' == 'true' else 'false'"])
        ),
    )

    bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="gz_sensor_bridge",
        output="screen",
        respawn=True,
        respawn_delay=10.0,
        parameters=[
            {
                "bridge.all": "false",
                "bridge.clock": "true",
            }
        ],
        arguments=list(BRIDGES),
        remappings=REMAPPINGS,
    )

    # The command. The bridge will not carry it: the Gazebo topic has a
    # subscriber and no publisher, and the bridge looks the topic's type up
    # before it starts. The forwarder publishes the message itself, so it
    # needs no lookup.
    base_command_forwarder = Node(
        package="robot_collab_gz_bridge",
        executable="twist_forwarder",
        name="gz_twist_forwarder",
        output="screen",
        parameters=[
            {
                "gz_topic": "/diff_drive_controller/cmd_vel",
                "ros_topic": "/diff_drive_controller/cmd_vel",
            }
        ],
    )

    base_odom_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        name="gz_base_odom_bridge",
        output="screen",
        arguments=list(BASE_BRIDGE_ODOM),
        respawn=True,
        respawn_delay=10.0,
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
            gz_gui,
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
            # No controller manager: gz_ros2_control's Fortress build reads
            # the controllers out of the server's own state and takes the
            # simulator down with it. The base is driven by gz-sim's own
            # diff-drive system, which is in the world file.
            bridge,
            base_command_forwarder,
            base_odom_bridge,
            # The world's ground truth. Gazebo Classic published it from a
            # plugin; in gz-sim it is a system of the world, and this node
            # is what puts it back on the topics the stack reads.
            Node(
                package="robot_collab_gz_bridge",
                executable="gz_pose_to_model_states",
                name="gz_pose_to_model_states",
                output="screen",
                parameters=[
                    {
                        "gz_topic": "/model/collab_mobile_base/pose",
                        "model_states_topic": "/model_states",
                        "link_states_topic": "/link_states",
                    }
                ],
            ),
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
                        "cmd_vel_topic": "/diff_drive_controller/cmd_vel",
                        "odom_topic": "/diff_drive_controller/odom",
                        # The base in gz-sim pivots on the spot: the
                        # wheels grip and the chassis answers. A station
                        # behind it is reached by turning there and
                        # driving off.
                        "pivot_capable": True,
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
