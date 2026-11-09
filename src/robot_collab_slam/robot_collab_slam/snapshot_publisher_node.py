"""Publish observation snapshots from the live camera.

The contract lives in agent_harness.contracts, but this node deliberately emits
the same JSON shape without importing it: the ROS package must stay buildable
without the harness. Fields are validated by tests/test_snapshot_builder.py on
the consumer side.

Topic: /agent/observation_snapshot  (std_msgs/String, JSON)
"""

from __future__ import annotations

import json
import time
import uuid

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String
from tf2_ros import Buffer, TransformException, TransformListener


class SnapshotPublisher(Node):
    def __init__(self) -> None:
        super().__init__("snapshot_publisher")
        self.declare_parameter("image_topic", "/camera/color/image_raw")
        self.declare_parameter("output_topic", "/agent/observation_snapshot")
        self.declare_parameter("frame_id", "map")
        self.declare_parameter("source_frame", "camera_link")
        self.declare_parameter("sensor_id", "d435")
        self.declare_parameter("calibration_version", "calib-default")
        self.declare_parameter("max_rate_hz", 5.0)

        self._frame_id = str(self.get_parameter("frame_id").value)
        self._source_frame = str(self.get_parameter("source_frame").value)
        self._sensor_id = str(self.get_parameter("sensor_id").value)
        self._calibration = str(self.get_parameter("calibration_version").value)
        self._min_period = 1.0 / max(0.1, float(self.get_parameter("max_rate_hz").value))
        self._last_emit = 0.0
        self._state_version = 0
        self._warned = False

        self._buffer = Buffer()
        self._listener = TransformListener(self._buffer, self)
        topic = str(self.get_parameter("image_topic").value)
        self._publisher = self.create_publisher(
            String, str(self.get_parameter("output_topic").value), 10
        )
        self.create_subscription(Image, topic, self._on_image, 5)
        self.get_logger().info(
            f"snapshot_publisher: {topic} -> {self._frame_id}->{self._source_frame} "
            f"(sensor {self._sensor_id}, calibration {self._calibration})"
        )

    def _on_image(self, msg: Image) -> None:
        now = time.time()
        if now - self._last_emit < self._min_period:
            return
        self._last_emit = now
        self._state_version += 1

        payload = {
            "image_width": msg.width,
            "image_height": msg.height,
            "encoding": msg.encoding,
            "camera_pose": None,
        }
        try:
            transform = self._buffer.lookup_transform(
                self._frame_id, self._source_frame, rclpy.time.Time()
            )
        except TransformException as error:
            # A snapshot without a pose cannot be acted on; do not publish it.
            if not self._warned:
                self.get_logger().warn(
                    f"waiting for {self._frame_id}->{self._source_frame}: {error}"
                )
                self._warned = True
            return
        self._warned = False
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        payload["camera_pose"] = [
            translation.x,
            translation.y,
            translation.z,
            rotation.x,
            rotation.y,
            rotation.z,
            rotation.w,
        ]

        snapshot = {
            "snapshot_id": f"snap-{uuid.uuid4().hex[:12]}",
            "taken_at": msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9,
            "frame_id": self._frame_id,
            "state_version": self._state_version,
            "sensor_id": self._sensor_id,
            "calibration_version": self._calibration,
            "payload": payload,
        }
        out = String()
        out.data = json.dumps(snapshot, ensure_ascii=False)
        self._publisher.publish(out)


def main() -> None:
    rclpy.init()
    node = SnapshotPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
