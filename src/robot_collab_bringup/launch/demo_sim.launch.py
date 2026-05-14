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
                package="robot_collab_core",
                executable="mission_state_machine",
                name="mission_state_machine",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_navigation",
                executable="nav_skill_server",
                name="nav_skill_server",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_manipulation",
                executable="arm_skill_server",
                name="arm_skill_server",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_perception",
                executable="tool_detector_node",
                name="tool_detector_node",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_perception",
                executable="face_auth_node",
                name="face_auth_node",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_perception",
                executable="gesture_recognizer_node",
                name="gesture_recognizer_node",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_hri",
                executable="voice_gateway_node",
                name="voice_gateway_node",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="robot_collab_agent",
                executable="agent_gateway_node",
                name="agent_gateway_node",
                output="screen",
                parameters=[params],
            ),
        ]
    )

