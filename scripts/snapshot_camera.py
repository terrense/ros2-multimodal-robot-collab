#!/usr/bin/env python3
"""One-shot grab of a single frame from a simulated camera topic.

    python3 scripts/snapshot_camera.py out.png                     # the robot's camera
    python3 scripts/snapshot_camera.py out.png /workshop/overhead/image_raw
"""

from __future__ import annotations

import sys

import numpy as np
import rclpy
from PIL import Image as PILImage
from rclpy.node import Node
from sensor_msgs.msg import Image

DEFAULT_TOPIC = "/camera/color/image_raw"


def main() -> None:
    output_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/camera_snapshot.png"
    topic = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TOPIC
    rclpy.init()
    node = Node("camera_snapshot")
    captured: list[Image] = []

    def _on_image(msg: Image) -> None:
        captured.append(msg)

    node.create_subscription(Image, topic, _on_image, 10)

    deadline = node.get_clock().now().nanoseconds + 15_000_000_000
    while not captured and node.get_clock().now().nanoseconds < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)

    if not captured:
        print(f"TIMEOUT: no image received on {topic}")
        node.destroy_node()
        rclpy.shutdown()
        sys.exit(1)

    msg = captured[-1]
    arr = np.frombuffer(msg.data, dtype=np.uint8).reshape((msg.height, msg.width, 3))
    if msg.encoding == "bgr8":
        arr = arr[:, :, ::-1]
    PILImage.fromarray(arr, "RGB").save(output_path)
    print(f"SAVED: {output_path} ({msg.width}x{msg.height}, encoding={msg.encoding})")
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
