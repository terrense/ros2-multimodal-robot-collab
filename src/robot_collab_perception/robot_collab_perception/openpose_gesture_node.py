from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

from robot_collab_interfaces.msg import ArmControlCommand, GestureCommand


class OpenPoseGestureNode(Node):
    """OpenPose hand-keypoint gesture recognizer for arm control."""

    GESTURE_TO_COMMAND = {
        "fist": ("arm_pause", False, "Fist detected: pause manipulator motion."),
        "palm": ("system_stop", True, "Open palm detected: stop arm and request shutdown."),
        "thumb_up": ("arm_start", False, "Thumb-up detected: start or resume work."),
    }

    def __init__(self) -> None:
        super().__init__("openpose_gesture_node")
        self.declare_parameter("camera_topic", "/camera/color/image_raw")
        self.declare_parameter("operator_id", "operator_001")
        self.declare_parameter("openpose_root", "third_party/openpose")
        self.declare_parameter("model_folder", "third_party/openpose/models")
        self.declare_parameter("min_keypoint_confidence", 0.25)
        self._bridge = None
        self._op = None
        self._wrapper = None
        self._gesture_pub = self.create_publisher(GestureCommand, "/hri/gesture_command", 10)
        self._arm_pub = self.create_publisher(ArmControlCommand, "/arm/control_command", 10)
        self._sub = self.create_subscription(
            Image,
            str(self.get_parameter("camera_topic").value),
            self._handle_image,
            10,
        )
        self.get_logger().info("OpenPose gesture node is ready.")

    def _ensure_backend(self) -> bool:
        if self._wrapper is not None:
            return True
        try:
            from cv_bridge import CvBridge
        except ImportError:
            self.get_logger().error("cv_bridge is required for OpenPose gesture recognition.")
            return False

        openpose_root = Path(str(self.get_parameter("openpose_root").value)).resolve()
        python_path = openpose_root / "build" / "python"
        if python_path.exists():
            sys.path.append(str(python_path))

        try:
            from openpose import pyopenpose as op
        except ImportError:
            self.get_logger().error(
                "OpenPose Python bindings were not found. Build third_party/openpose with BUILD_PYTHON=ON first."
            )
            return False

        params = {
            "model_folder": str(Path(str(self.get_parameter("model_folder").value)).resolve()),
            "hand": True,
            "face": False,
            "body": 1,
        }
        wrapper = op.WrapperPython()
        wrapper.configure(params)
        wrapper.start()
        self._bridge = CvBridge()
        self._op = op
        self._wrapper = wrapper
        self.get_logger().info("OpenPose backend initialized for gesture recognition.")
        return True

    def _handle_image(self, msg: Image) -> None:
        if not self._ensure_backend():
            return
        try:
            frame = self._bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        except Exception as exc:
            self.get_logger().warn(f"Failed to convert image for OpenPose: {exc}")
            return

        datum = self._op.Datum()
        datum.cvInputData = frame
        self._wrapper.emplaceAndPop(self._op.VectorDatum([datum]))

        gesture = self._classify_from_datum(datum)
        if gesture:
            self._publish_gesture(msg, gesture)

    def _classify_from_datum(self, datum: Any) -> str | None:
        for hand_points in (getattr(datum, "handKeypoints", None) or []):
            if hand_points is None or len(hand_points) == 0:
                continue
            for person_hand in hand_points:
                gesture = self._classify_hand(person_hand)
                if gesture:
                    return gesture
        return None

    def _classify_hand(self, keypoints) -> str | None:
        min_conf = float(self.get_parameter("min_keypoint_confidence").value)
        if len(keypoints) < 21:
            return None
        if sum(1 for point in keypoints if float(point[2]) >= min_conf) < 12:
            return None

        finger_tips = [8, 12, 16, 20]
        finger_pips = [6, 10, 14, 18]
        extended = [
            float(keypoints[tip][1]) < float(keypoints[pip][1])
            for tip, pip in zip(finger_tips, finger_pips)
        ]
        thumb_up = float(keypoints[4][1]) < float(keypoints[3][1]) and sum(extended) <= 1
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
        self.get_logger().info(f"OpenPose gesture {gesture} -> {command}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = OpenPoseGestureNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
