from __future__ import annotations

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

from robot_collab_interfaces.msg import ArmControlCommand, GestureCommand


class MediapipeGestureNode(Node):
    """Camera-based hand gesture recognizer for arm control."""

    GESTURE_TO_COMMAND = {
        "fist": ("arm_pause", False, "Fist detected: pause manipulator motion."),
        "palm": ("system_stop", True, "Open palm detected: stop arm and request shutdown."),
        "thumb_up": ("arm_start", False, "Thumb-up detected: start or resume work."),
    }

    def __init__(self) -> None:
        super().__init__("mediapipe_gesture_node")
        self.declare_parameter("camera_topic", "/camera/color/image_raw")
        self.declare_parameter("operator_id", "operator_001")
        self.declare_parameter("min_confidence", 0.65)
        self._bridge = None
        self._hands = None
        self._gesture_pub = self.create_publisher(GestureCommand, "/hri/gesture_command", 10)
        self._arm_pub = self.create_publisher(ArmControlCommand, "/arm/control_command", 10)
        self._sub = self.create_subscription(
            Image,
            str(self.get_parameter("camera_topic").value),
            self._handle_image,
            10,
        )
        self.get_logger().info("MediaPipe gesture node is ready.")

    def _ensure_backend(self) -> bool:
        if self._bridge is not None and self._hands is not None:
            return True
        try:
            from cv_bridge import CvBridge
            import mediapipe as mp
        except ImportError:
            self.get_logger().error("Install cv_bridge and mediapipe to run camera-based gesture recognition.")
            return False
        self._bridge = CvBridge()
        self._hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=float(self.get_parameter("min_confidence").value),
            min_tracking_confidence=float(self.get_parameter("min_confidence").value),
        )
        return True

    def _handle_image(self, msg: Image) -> None:
        if not self._ensure_backend():
            return
        try:
            frame = self._bridge.imgmsg_to_cv2(msg, desired_encoding="rgb8")
        except Exception as exc:
            self.get_logger().warn(f"Failed to convert image for gesture recognition: {exc}")
            return
        results = self._hands.process(frame)
        if not results.multi_hand_landmarks:
            return
        gesture = self._classify(results.multi_hand_landmarks[0].landmark)
        if gesture is None:
            return
        self._publish_gesture(msg, gesture)

    def _classify(self, landmarks) -> str | None:
        finger_tips = [8, 12, 16, 20]
        finger_pips = [6, 10, 14, 18]
        extended = [landmarks[tip].y < landmarks[pip].y for tip, pip in zip(finger_tips, finger_pips)]
        thumb_up = landmarks[4].y < landmarks[3].y and sum(extended) <= 1
        if thumb_up:
            return "thumb_up"
        if all(extended):
            return "palm"
        if not any(extended):
            return "fist"
        return None

    def _publish_gesture(self, image_msg: Image, gesture: str) -> None:
        command, emergency, detail = self.GESTURE_TO_COMMAND[gesture]
        operator_id = str(self.get_parameter("operator_id").value)
        confidence = 0.90

        gesture_msg = GestureCommand()
        gesture_msg.header = image_msg.header
        gesture_msg.command = command
        gesture_msg.confidence = confidence
        gesture_msg.operator_id = operator_id
        gesture_msg.source = self.get_name()
        self._gesture_pub.publish(gesture_msg)

        arm_msg = ArmControlCommand()
        arm_msg.header = image_msg.header
        arm_msg.command = command
        arm_msg.operator_id = operator_id
        arm_msg.source = self.get_name()
        arm_msg.confidence = confidence
        arm_msg.emergency = emergency
        arm_msg.detail = detail
        self._arm_pub.publish(arm_msg)
        self.get_logger().info(f"Gesture {gesture} -> {command}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MediapipeGestureNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

