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

    sim_share = FindPackageShare("robot_collab_sim")
    robot_description_path = PathJoinSubstitution([sim_share, "urdf", "collab_mobile_base.urdf.xacro"])
    default_world_path = PathJoinSubstitution([sim_share, "worlds", "collab_lab.world"])

    robot_description = ParameterValue(
        Command(["xacro ", robot_description_path]),
        value_type=str,
    )

    gazebo_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("gazebo_ros"), "launch", "gazebo.launch.py"])
        ),
        launch_arguments={
            "world": world,
            "verbose": "true",
        }.items(),
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument("robot_name", default_value="collab_mobile_base"),
            DeclareLaunchArgument("world", default_value=default_world_path),
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
                    "-z",
                    "0.08",
                ],
            ),
        ]
    )
