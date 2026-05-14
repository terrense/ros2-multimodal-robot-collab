import rclpy
from rclpy.node import Node

from robot_collab_interfaces.msg import ArmControlCommand, GestureCommand


class GestureRecognizerNode(Node):
    """Publishes gesture commands and arm-control intents.

    Set `auto_emit` to false for real deployments and publish commands from a
    MediaPipe/OpenCV gesture pipeline instead.
    """

    GESTURE_TO_COMMAND = {
        "fist": ("arm_pause", False, "Fist detected: pause manipulator motion."),
        "palm": ("system_stop", True, "Open palm detected: stop arm and request shutdown."),
        "thumb_up": ("arm_start", False, "Thumb-up detected: start or resume work."),
        "confirm_delivery": ("arm_start", False, "Confirmation gesture mapped to start command."),
        "cancel": ("system_stop", True, "Cancel gesture mapped to stop command."),
    }

    def __init__(self) -> None:
        super().__init__("gesture_recognizer_node")
        self.declare_parameter("auto_emit", False)
        self.declare_parameter("emit_interval_sec", 8.0)
        self.declare_parameter("gesture", "thumb_up")
        self.declare_parameter("operator_id", "operator_001")
        self._gesture_pub = self.create_publisher(GestureCommand, "/hri/gesture_command", 10)
        self._arm_pub = self.create_publisher(ArmControlCommand, "/arm/control_command", 10)
        if bool(self.get_parameter("auto_emit").value):
            interval = max(1.0, float(self.get_parameter("emit_interval_sec").value))
            self._timer = self.create_timer(interval, self._publish_gesture)
            self.get_logger().info("Gesture recognizer stub auto-emission is enabled.")
        else:
            self._timer = None
            self.get_logger().info("Gesture recognizer stub is idle until a real pipeline is connected.")

    def _publish_gesture(self) -> None:
        gesture = str(self.get_parameter("gesture").value)
        operator_id = str(self.get_parameter("operator_id").value)
        self.publish_recognition(gesture, 0.91, operator_id)

    def publish_recognition(self, gesture: str, confidence: float, operator_id: str) -> None:
        command, emergency, detail = self.GESTURE_TO_COMMAND.get(
            gesture,
            ("unknown", False, f"Unmapped gesture: {gesture}"),
        )

        msg = GestureCommand()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.command = command
        msg.confidence = confidence
        msg.operator_id = operator_id
        msg.source = self.get_name()
        self._gesture_pub.publish(msg)

        arm_msg = ArmControlCommand()
        arm_msg.header = msg.header
        arm_msg.command = command
        arm_msg.operator_id = operator_id
        arm_msg.source = self.get_name()
        arm_msg.confidence = confidence
        arm_msg.emergency = emergency
        arm_msg.detail = detail
        self._arm_pub.publish(arm_msg)
        self.get_logger().info(f"Gesture {gesture} -> {command}: {detail}")


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
