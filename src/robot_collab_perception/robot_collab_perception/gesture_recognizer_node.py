import rclpy
from rclpy.node import Node

from robot_collab_interfaces.msg import GestureCommand


class GestureRecognizerNode(Node):
    """Publishes simulated gesture commands.

    Set `auto_emit` to false for real deployments and publish commands from a
    MediaPipe/OpenCV gesture pipeline instead.
    """

    def __init__(self) -> None:
        super().__init__("gesture_recognizer_node")
        self.declare_parameter("auto_emit", False)
        self.declare_parameter("emit_interval_sec", 8.0)
        self.declare_parameter("command", "confirm_delivery")
        self.declare_parameter("operator_id", "operator_001")
        self._pub = self.create_publisher(GestureCommand, "/hri/gesture_command", 10)
        if bool(self.get_parameter("auto_emit").value):
            interval = max(1.0, float(self.get_parameter("emit_interval_sec").value))
            self._timer = self.create_timer(interval, self._publish_gesture)
            self.get_logger().info("Gesture recognizer stub auto-emission is enabled.")
        else:
            self._timer = None
            self.get_logger().info("Gesture recognizer stub is idle until a real pipeline is connected.")

    def _publish_gesture(self) -> None:
        msg = GestureCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.command = str(self.get_parameter("command").value)
        msg.confidence = 0.91
        msg.operator_id = str(self.get_parameter("operator_id").value)
        msg.source = self.get_name()
        self._pub.publish(msg)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = GestureRecognizerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

