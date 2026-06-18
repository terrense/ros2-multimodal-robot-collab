from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import rclpy
from builtin_interfaces.msg import Time
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from sensor_msgs.msg import Image

from robot_collab_interfaces.msg import ToolDetection


class Yolov8ToolDetectorNode(Node):
    """YOLOv8-based tool detector adapter.

    The Ultralytics package is loaded lazily so the ROS2 workspace still builds
    when the third-party dependency has not been fetched yet.

    The node runs in one of two input modes:

    - live camera mode (default): subscribe to ``camera_topic`` and run YOLO on
      each frame. Use this once the scene contains objects a COCO model can see.
    - static image mode: set ``image_path`` to a real photo. The node runs the
      same pretrained YOLOv8 inference on that image on a timer. This guarantees
      the end-to-end pipeline gets a real detection even when the Gazebo scene
      only has featureless primitives that COCO cannot recognize.

    ``class_to_tool_json`` maps detector class labels onto project tool ids. A
    ``"*"`` wildcard key maps every otherwise-unmapped class onto a single tool
    id, which is handy for wiring up the full chain before real tool weights
    exist: any detection unblocks the mission as the requested tool.
    """

    WILDCARD = "*"

    def __init__(self) -> None:
        super().__init__("yolov8_tool_detector_node")
        self.declare_parameter("model_path", "yolov8n.pt")
        self.declare_parameter("camera_topic", "/camera/color/image_raw")
        self.declare_parameter("source_frame", "camera_link")
        self.declare_parameter("min_confidence", 0.45)
        self.declare_parameter(
            "class_to_tool_json",
            '{"wrench":"wrench","screwdriver":"screwdriver","hex_key":"hex_key_3mm"}',
        )
        # Static image mode: when set, run inference on this real photo instead of
        # the live camera so the chain always produces a detection.
        self.declare_parameter("image_path", "")
        self.declare_parameter("publish_hz", 2.0)

        self._model: Any | None = None
        self._class_to_tool = self._load_tool_map()
        self._pub = self.create_publisher(ToolDetection, "/perception/tool_detections", 10)

        image_path = str(self.get_parameter("image_path").value).strip()
        if image_path:
            self._image_path = Path(image_path)
            publish_hz = max(0.1, float(self.get_parameter("publish_hz").value))
            self._timer = self.create_timer(1.0 / publish_hz, self._process_static_image)
            self.get_logger().info(
                f"YOLOv8 tool detector running in static-image mode: {self._image_path}"
            )
        else:
            self._sub = self.create_subscription(
                Image,
                str(self.get_parameter("camera_topic").value),
                self._handle_image,
                10,
            )
            self.get_logger().info("YOLOv8 tool detector adapter is ready (live camera mode).")

    def _load_tool_map(self) -> dict[str, str]:
        try:
            return json.loads(str(self.get_parameter("class_to_tool_json").value))
        except json.JSONDecodeError:
            self.get_logger().warn("Invalid class_to_tool_json; using empty mapping.")
            return {}

    def _map_tool(self, label: str) -> str:
        if label in self._class_to_tool:
            return self._class_to_tool[label]
        if self.WILDCARD in self._class_to_tool:
            return self._class_to_tool[self.WILDCARD]
        return label

    def _load_model(self):
        if self._model is not None:
            return self._model
        try:
            from ultralytics import YOLO
        except ImportError:
            self.get_logger().error(
                "Ultralytics is not installed. Run scripts/fetch_models.sh or "
                "'pip install ultralytics' first."
            )
            return None
        model_path = str(self.get_parameter("model_path").value)
        # Ultralytics auto-downloads bare weight names (e.g. 'yolov8n.pt') when the
        # file is absent and the network is reachable.
        self._model = YOLO(model_path)
        self.get_logger().info(f"Loaded YOLOv8 model: {model_path}")
        return self._model

    def _process_static_image(self) -> None:
        try:
            import cv2
        except ImportError:
            self.get_logger().error("python3-opencv (cv2) is required for static-image mode.")
            return
        if not self._image_path.exists():
            self.get_logger().warn(
                f"Static image not found: {self._image_path}. Run scripts/fetch_models.sh."
            )
            return
        image = cv2.imread(str(self._image_path))
        if image is None:
            self.get_logger().warn(f"Failed to read image: {self._image_path}")
            return
        frame_id = str(self.get_parameter("source_frame").value)
        self._run_inference(image, self.get_clock().now().to_msg(), frame_id)

    def _handle_image(self, msg: Image) -> None:
        try:
            import numpy as np
        except ImportError:
            self.get_logger().error("numpy is required by the YOLOv8 adapter.")
            return

        image = np.frombuffer(msg.data, dtype=np.uint8)
        if msg.encoding in ("rgb8", "bgr8"):
            image = image.reshape((msg.height, msg.width, 3))
            if msg.encoding == "rgb8":
                image = image[:, :, ::-1]
        else:
            self.get_logger().warn(f"Unsupported image encoding for YOLO adapter: {msg.encoding}")
            return

        frame_id = str(self.get_parameter("source_frame").value)
        self._run_inference(image, msg.header.stamp, frame_id)

    def _run_inference(self, image_bgr: Any, stamp: Time, frame_id: str) -> None:
        model = self._load_model()
        if model is None:
            return

        results = model.predict(image_bgr, verbose=False)
        if not results:
            return

        min_conf = float(self.get_parameter("min_confidence").value)
        names = results[0].names
        boxes = getattr(results[0], "boxes", None)
        if boxes is None:
            return

        height, width = image_bgr.shape[0], image_bgr.shape[1]
        published = 0
        for box in boxes:
            confidence = float(box.conf[0])
            if confidence < min_conf:
                continue
            cls_id = int(box.cls[0])
            label = str(names.get(cls_id, cls_id)) if isinstance(names, dict) else str(names[cls_id])
            tool_id = self._map_tool(label)
            detection = ToolDetection()
            detection.header.stamp = stamp
            detection.header.frame_id = frame_id
            detection.tool_id = tool_id
            detection.label = label
            detection.confidence = confidence
            detection.tool_pose = self._pose_from_bbox(stamp, frame_id, width, height, box)
            detection.source_frame = frame_id
            detection.detector_id = self.get_name()
            self._pub.publish(detection)
            published += 1
        if published:
            self.get_logger().info(f"Published {published} tool detection(s).")

    def _pose_from_bbox(self, stamp: Time, frame_id: str, width: int, height: int, box: Any) -> PoseStamped:
        pose = PoseStamped()
        pose.header.stamp = stamp
        pose.header.frame_id = frame_id
        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
        cx = (x1 + x2) * 0.5
        cy = (y1 + y2) * 0.5
        pose.pose.position.x = (cx / max(1.0, float(width))) - 0.5
        pose.pose.position.y = (cy / max(1.0, float(height))) - 0.5
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
