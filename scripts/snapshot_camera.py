#!/usr/bin/env python3
"""One-shot grab of a single frame from the simulated camera topic."""

from __future__ import annotations

import sys

import numpy as np
import rclpy
from PIL import Image as PILImage
from rclpy.node import Node
from sensor_msgs.msg import Image


def main() -> None:
    output_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/camera_snapshot.png"
    rclpy.init()
    node = Node("camera_snapshot")
    captured: list[Image] = []

    def _on_image(msg: Image) -> None:
        captured.append(msg)

    node.create_subscription(Image, "/camera/color/image_raw", _on_image, 10)

    deadline = node.get_clock().now().nanoseconds + 15_000_000_000
    while not captured and node.get_clock().now().nanoseconds < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)

    if not captured:
        print("TIMEOUT: no image received on /camera/color/image_raw")
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
