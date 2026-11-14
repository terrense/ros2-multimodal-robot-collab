from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time")
    robot_name = LaunchConfiguration("robot_name")
    world = LaunchConfiguration("world")
    gui = LaunchConfiguration("gui")
    spawn_yaw = LaunchConfiguration("spawn_yaw")

    sim_share = FindPackageShare("robot_collab_sim")
    robot_description_path = PathJoinSubstitution([sim_share, "urdf", "collab_mobile_base.urdf.xacro"])
    default_world_path = PathJoinSubstitution([sim_share, "worlds", "collab_lab.world"])

    # Not a plain `xacro` call: the description has to survive being handed to
    # the controller manager as a parameter override, and rcl parses the value
    # of an override as YAML -- where a colon followed by a space (an XML
    # comment) ends the value. `robot_description` strips the comments and
    # fails loudly if something else in the description cannot be carried.
    robot_description = ParameterValue(
        Command(["python3 -m robot_collab_sim.robot_description ", robot_description_path]),
        value_type=str,
    )

    gazebo_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("gazebo_ros"), "launch", "gazebo.launch.py"])
        ),
        launch_arguments={
            "world": world,
            "verbose": "true",
            # gui:=false starts gzserver only. A machine with no display (a
            # container, a CI runner, WSLg not started) can still run the whole
            # stack and be driven by the runtime -- which is what the CI job and
            # the headless calibration run need.
            "gui": gui,
        }.items(),
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument("robot_name", default_value="collab_mobile_base"),
            DeclareLaunchArgument("world", default_value=default_world_path),
            DeclareLaunchArgument("gui", default_value="true"),
            DeclareLaunchArgument(
                "spawn_yaw",
                default_value="0.0",
                description=(
                    "The heading the base is spawned with. The bringup passes the "
                    "home station's yaw, so that the robot starts where the "
                    "stations say it starts and faces what it is meant to see."
                ),
            ),
            gazebo_launch,
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
            Node(
                package="gazebo_ros",
                executable="spawn_entity.py",
                name="spawn_collab_mobile_base",
                output="screen",
                arguments=[
                    "-entity",
                    robot_name,
                    "-topic",
                    "robot_description",
                    "-x",
                    "0.0",
                    "-y",
                    "0.0",
                    # On the ground, not above it: the wheels touch, and a
                    # robot that spawns in the air lands on its wheels with
                    # a contact the solver cannot resolve.
                    "-z",
                    "0.0",
                    # The heading the base starts with. A camera that starts
                    # pointing at a wall sees nothing, and a measurement that
                    # needs the robot to look at the tool needs it to start
                    # looking at the tool.
                    "-Y",
                    spawn_yaw,
                ],
            ),
            # The wheels are commanded through ros2_control. Without these two
            # spawners the base has no motors: the joints spin and the robot
            # slides a couple of centimetres, which looks like a robot that
            # moves and is not one.
            #
            # The controller manager lives inside the gzserver process and
            # only appears once the world has loaded and the robot has been
            # spawned -- a minute and a half on a cold start, longer on a CI
            # runner. The spawners' own timeout is 60 s, so they have to be
            # told to wait longer than the default or they give up before the
            # manager exists.
            Node(
                package="controller_manager",
                executable="spawner",
                name="spawn_joint_state_broadcaster",
                output="screen",
                arguments=[
                    "joint_state_broadcaster",
                    "--controller-manager",
                    "/controller_manager",
                    "--controller-manager-timeout",
                    "300",
                ],
            ),
            Node(
                package="controller_manager",
                executable="spawner",
                name="spawn_diff_drive_controller",
                output="screen",
                arguments=[
                    "diff_drive_controller",
                    "--controller-manager",
                    "/controller_manager",
                    "--controller-manager-timeout",
                    "300",
                    # A controller reads its parameters from its own node, and
                    # the spawner is what hands them over. The file is named
                    # here rather than inside the manager's configuration
                    # because only the launch knows where the package was
                    # installed.
                    "--param-file",
                    PathJoinSubstitution([sim_share, "config", "diff_drive_controller.yaml"]),
                ],
            ),
        ]
    )
