from __future__ import annotations

import json
from typing import Any

import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from sensor_msgs.msg import Image

from robot_collab_interfaces.msg import ToolDetection


class Yolov8ToolDetectorNode(Node):
    """YOLOv8-based tool detector adapter.

    The Ultralytics package is loaded lazily so the ROS2 workspace still builds
    when the third-party dependency has not been fetched yet.
    """

    def __init__(self) -> None:
        super().__init__("yolov8_tool_detector_node")
        self.declare_parameter("model_path", "models/yolov8n.pt")
        self.declare_parameter("camera_topic", "/camera/color/image_raw")
        self.declare_parameter("source_frame", "tool_camera_link")
        self.declare_parameter("min_confidence", 0.45)
        self.declare_parameter("class_to_tool_json", '{"wrench":"wrench","screwdriver":"screwdriver","hex_key":"hex_key_3mm"}')
        self._model: Any | None = None
        self._class_to_tool = self._load_tool_map()
        self._pub = self.create_publisher(ToolDetection, "/perception/tool_detections", 10)
        self._sub = self.create_subscription(
            Image,
            str(self.get_parameter("camera_topic").value),
            self._handle_image,
            10,
        )
        self.get_logger().info("YOLOv8 tool detector adapter is ready.")

    def _load_tool_map(self) -> dict[str, str]:
        try:
            return json.loads(str(self.get_parameter("class_to_tool_json").value))
        except json.JSONDecodeError:
            self.get_logger().warn("Invalid class_to_tool_json; using empty mapping.")
            return {}

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from ultralytics import YOLO
        except ImportError:
            self.get_logger().error(
                "Ultralytics is not installed. Fetch third_party/ultralytics or install the package first."
            )
            return None
        model_path = str(self.get_parameter("model_path").value)
        self._model = YOLO(model_path)
        self.get_logger().info(f"Loaded YOLOv8 model: {model_path}")
        return self._model

    def _handle_image(self, msg: Image) -> None:
        model = self._load_model()
        if model is None:
            return
        try:
            import numpy as np
        except ImportError:
            self.get_logger().error("numpy is required by the YOLOv8 adapter.")
            return

        image = np.frombuffer(msg.data, dtype=np.uint8)
        if msg.encoding in ("rgb8", "bgr8"):
            image = image.reshape((msg.height, msg.width, 3))
        else:
            self.get_logger().warn(f"Unsupported image encoding for YOLO adapter: {msg.encoding}")
            return

        results = model.predict(image, verbose=False)
        if not results:
            return

        min_conf = float(self.get_parameter("min_confidence").value)
        names = results[0].names
        boxes = getattr(results[0], "boxes", None)
        if boxes is None:
            return

        for box in boxes:
            confidence = float(box.conf[0])
            if confidence < min_conf:
                continue
            cls_id = int(box.cls[0])
            label = str(names.get(cls_id, cls_id))
            tool_id = self._class_to_tool.get(label, label)
            detection = ToolDetection()
            detection.header.stamp = self.get_clock().now().to_msg()
            detection.header.frame_id = str(self.get_parameter("source_frame").value)
            detection.tool_id = tool_id
            detection.label = label
            detection.confidence = confidence
            detection.tool_pose = self._pose_from_bbox(msg, box)
            detection.source_frame = detection.header.frame_id
            detection.detector_id = self.get_name()
            self._pub.publish(detection)

    def _pose_from_bbox(self, msg: Image, box: Any) -> PoseStamped:
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = str(self.get_parameter("source_frame").value)
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        cx = (x1 + x2) * 0.5
        cy = (y1 + y2) * 0.5
        pose.pose.position.x = (cx / max(1.0, float(msg.width))) - 0.5
        pose.pose.position.y = (cy / max(1.0, float(msg.height))) - 0.5
        pose.pose.position.z = 0.0
        pose.pose.orientation.w = 1.0
        return pose


def main(args=None) -> None:
    rclpy.init(args=args)
    node = Yolov8ToolDetectorNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
