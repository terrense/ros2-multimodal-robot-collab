import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node

from robot_collab_interfaces.msg import ToolDetection


class ToolDetectorNode(Node):
    """Publishes simulated tool detections.

    Replace this node with an OpenCV/YOLO detector that publishes the same
    `ToolDetection` message to keep downstream nodes unchanged.
    """

    def __init__(self) -> None:
        super().__init__("tool_detector_node")
        self.declare_parameter("tool_id", "hex_key_3mm")
        self.declare_parameter("tool_label", "3mm hex key")
        self.declare_parameter("source_frame", "tool_camera_link")
        self.declare_parameter("publish_hz", 1.0)
        self.declare_parameter("confidence", 0.88)
        self._pub = self.create_publisher(ToolDetection, "/perception/tool_detections", 10)

        publish_hz = max(0.1, float(self.get_parameter("publish_hz").value))
        self._timer = self.create_timer(1.0 / publish_hz, self._publish_detection)
        self.get_logger().info("Tool detector stub is publishing detections.")

    def _publish_detection(self) -> None:
        frame = str(self.get_parameter("source_frame").value)
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = frame
        pose.pose.position.x = 0.42
        pose.pose.position.y = -0.12
        pose.pose.position.z = 0.08
        pose.pose.orientation.w = 1.0

        msg = ToolDetection()
        msg.header = pose.header
        msg.tool_id = str(self.get_parameter("tool_id").value)
        msg.label = str(self.get_parameter("tool_label").value)
        msg.confidence = float(self.get_parameter("confidence").value)
        msg.tool_pose = pose
        msg.source_frame = frame
        msg.detector_id = self.get_name()
        self._pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ToolDetectorNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

