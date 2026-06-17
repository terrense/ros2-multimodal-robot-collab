from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time")
    nav_backend = LaunchConfiguration("nav_backend")

    bringup_share = FindPackageShare("robot_collab_bringup")
    params = PathJoinSubstitution([bringup_share, "config", "demo_params.yaml"])
    stations = PathJoinSubstitution([bringup_share, "config", "stations.yaml"])

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("robot_collab_sim"), "launch", "gazebo_world.launch.py"])
        ),
        launch_arguments={"use_sim_time": use_sim_time}.items(),
    )

    common_params = [params, {"use_sim_time": use_sim_time}]

    return LaunchDescription(
        [
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument(
                "nav_backend",
                default_value="simulated",
                description="Use 'simulated' for API demo or 'nav2' to dispatch NavigateToPose goals.",
            ),
            gazebo,
            Node(
                package="robot_collab_core",
                executable="mission_state_machine",
                name="mission_state_machine",
                output="screen",
                parameters=common_params,
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
                        "backend": nav_backend,
                        "station_file": stations,
                    },
                ],
            ),
            Node(
                package="robot_collab_manipulation",
                executable="arm_skill_server",
                name="arm_skill_server",
                output="screen",
                parameters=common_params,
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
                    },
                ],
            ),
            Node(
                package="robot_collab_perception",
                executable="face_auth_node",
                name="face_auth_node",
                output="screen",
                parameters=common_params,
            ),
            Node(
                package="robot_collab_perception",
                executable="gesture_recognizer_node",
                name="gesture_recognizer_node",
                output="screen",
                parameters=common_params,
            ),
            Node(
                package="robot_collab_hri",
                executable="voice_gateway_node",
                name="voice_gateway_node",
                output="screen",
                parameters=common_params,
            ),
            Node(
                package="robot_collab_agent",
                executable="agent_gateway_node",
                name="agent_gateway_node",
                output="screen",
                parameters=common_params,
            ),
            Node(
                package="robot_collab_slam",
                executable="vins_mono_bridge_node",
                name="vins_mono_bridge_node",
                output="screen",
                parameters=[
                    params,
                    {
                        "use_sim_time": use_sim_time,
                        "vins_odometry_topic": "/odom",
                        "pose_topic": "/slam/vins_pose",
                        "status_topic": "/slam/vins_status",
                        "publish_tf": False,
                        "map_frame": "map",
                        "base_frame": "base_footprint",
                    },
                ],
            ),
        ]
    )
