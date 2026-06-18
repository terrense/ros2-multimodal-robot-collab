from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time")
    nav_backend = LaunchConfiguration("nav_backend")
    start_slam = LaunchConfiguration("start_slam")
    start_nav2 = LaunchConfiguration("start_nav2")
    camera_topic = LaunchConfiguration("camera_topic")
    tool_image = LaunchConfiguration("tool_image")

    bringup_share = FindPackageShare("robot_collab_bringup")
    params = PathJoinSubstitution([bringup_share, "config", "demo_params.yaml"])
    stations = PathJoinSubstitution([bringup_share, "config", "stations.yaml"])
    nav2_params = PathJoinSubstitution([bringup_share, "config", "nav2_params.yaml"])
    slam_params = PathJoinSubstitution([bringup_share, "config", "slam_toolbox_params.yaml"])

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("robot_collab_sim"), "launch", "gazebo_world.launch.py"])
        ),
        launch_arguments={"use_sim_time": use_sim_time}.items(),
    )

    slam = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("slam_toolbox"), "launch", "online_async_launch.py"])
        ),
        condition=IfCondition(start_slam),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "slam_params_file": slam_params,
        }.items(),
    )

    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("nav2_bringup"), "launch", "navigation_launch.py"])
        ),
        condition=IfCondition(start_nav2),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "params_file": nav2_params,
            "autostart": "true",
        }.items(),
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
            DeclareLaunchArgument(
                "start_slam",
                default_value="false",
                description="Start Slam Toolbox to publish map -> odom from the Gazebo laser scan.",
            ),
            DeclareLaunchArgument(
                "start_nav2",
                default_value="false",
                description="Start Nav2 navigation servers for station-level NavigateToPose goals.",
            ),
            DeclareLaunchArgument(
                "camera_topic",
                default_value="/camera/color/image_raw",
                description="Live RGB camera topic used when tool_image is empty.",
            ),
            DeclareLaunchArgument(
                "tool_image",
                default_value="models/coco_sample.jpg",
                description=(
                    "Real photo for YOLOv8 static-image mode so the chain always gets a "
                    "detection. Set to '' to run YOLO on the live Gazebo camera instead."
                ),
            ),
            gazebo,
            slam,
            nav2,
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
                executable="yolov8_tool_detector_node",
                name="yolov8_tool_detector_node",
                output="screen",
                parameters=[
                    params,
                    {
                        "use_sim_time": use_sim_time,
                        "camera_topic": camera_topic,
                        "source_frame": "camera_link",
                        "image_path": tool_image,
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
