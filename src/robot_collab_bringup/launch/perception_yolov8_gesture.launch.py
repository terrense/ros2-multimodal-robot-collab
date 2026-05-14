from launch import LaunchDescription
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution


def generate_launch_description():
    params = PathJoinSubstitution(
        [FindPackageShare("robot_collab_bringup"), "config", "demo_params.yaml"]
    )

    return LaunchDescription(
        [
            Node(
                package="robot_collab_perception",
                executable="yolov8_tool_detector_node",
                name="yolov8_tool_detector_node",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_perception",
                executable="mediapipe_gesture_node",
                name="mediapipe_gesture_node",
                output="screen",
                parameters=[params],
            ),
        ]
    )
