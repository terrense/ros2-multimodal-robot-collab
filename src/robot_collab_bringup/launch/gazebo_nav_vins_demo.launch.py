from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time")
    nav_backend = LaunchConfiguration("nav_backend")
    start_slam = LaunchConfiguration("start_slam")
    start_nav2 = LaunchConfiguration("start_nav2")
    use_yolo = LaunchConfiguration("use_yolo")

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
                "use_yolo",
                default_value="false",
                description=(
                    "Run the real YOLOv8 detector on the live camera instead of the "
                    "hardcoded tool_detector stub. Requires the ultralytics package and "
                    "models/yolov8n.pt, plus a COCO-recognizable object in the scene."
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
            # Tool detection has two interchangeable backends publishing the same
            # /perception/tool_detections contract. Default is the offline-safe
            # stub; use_yolo:=true swaps in real YOLOv8 inference on the camera.
            Node(
                package="robot_collab_perception",
                executable="tool_detector_node",
                name="tool_detector_node",
                output="screen",
                condition=UnlessCondition(use_yolo),
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
                executable="yolov8_tool_detector_node",
                name="yolov8_tool_detector_node",
                output="screen",
                condition=IfCondition(use_yolo),
                parameters=[
                    params,
                    {
                        "use_sim_time": use_sim_time,
                        # The Gazebo RGB camera publishes /camera/color/image_raw in
                        # the camera_link frame defined by the robot URDF.
                        "camera_topic": "/camera/color/image_raw",
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
