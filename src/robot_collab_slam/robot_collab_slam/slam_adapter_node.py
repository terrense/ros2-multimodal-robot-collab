"""Generic SLAM adapter: backend pose in, `map -> odom` out.

The point of this node is generality. Any SLAM backend — VINS, ORB-SLAM3,
RTAB-Map odometry, LIO-SAM, or a future algorithm — only has to publish a
`nav_msgs/Odometry` whose `header.frame_id` is the map frame and whose
`child_frame_id` is the robot base. This node composes it with the robot's own
`odom -> base` transform and publishes `map -> odom`, which is the single
contract Nav2 consumes.

Frames are parameters, not code.
"""

from __future__ import annotations

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from tf2_ros import Buffer, TransformBroadcaster, TransformException, TransformListener

from .slam_math import Quat, Transform, map_to_odom


def _to_transform(msg) -> Transform:
    translation = msg.transform.translation
    rotation = msg.transform.rotation
    return Transform(
        (translation.x, translation.y, translation.z),
        Quat(rotation.x, rotation.y, rotation.z, rotation.w),
    )


class SlamAdapter(Node):
    def __init__(self) -> None:
        super().__init__("slam_adapter")
        self.declare_parameter("pose_topic", "/slam/pose")
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("publish_rate_hz", 20.0)

        self._map_frame = self.get_parameter("map_frame").value
        self._odom_frame = self.get_parameter("odom_frame").value
        self._base_frame = self.get_parameter("base_frame").value

        self._buffer = Buffer()
        self._listener = TransformListener(self._buffer, self)
        self._broadcaster = TransformBroadcaster(self)
        self._latest: Transform | None = None
        self._warned = False

        topic = self.get_parameter("pose_topic").value
        self.create_subscription(Odometry, topic, self._on_pose, 10)
        rate = float(self.get_parameter("publish_rate_hz").value)
        self.create_timer(1.0 / max(rate, 1.0), self._publish)
        self.get_logger().info(
            f"slam_adapter: {topic} -> {self._map_frame}->{self._odom_frame} "
            f"(base {self._base_frame})"
        )

    def _on_pose(self, msg: Odometry) -> None:
        pose = msg.pose.pose
        self._latest = Transform(
            (pose.position.x, pose.position.y, pose.position.z),
            Quat(
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            ),
        )

    def _publish(self) -> None:
        if self._latest is None:
            return
        try:
            odom_base = _to_transform(
                self._buffer.lookup_transform(
                    self._odom_frame, self._base_frame, rclpy.time.Time()
                )
            )
        except TransformException as error:
            if not self._warned:
                self.get_logger().warn(f"waiting for {self._odom_frame}->{self._base_frame}: {error}")
                self._warned = True
            return

        corrected = map_to_odom(self._latest, odom_base)
        transform = TransformStamped()
        transform.header.stamp = self.get_clock().now().to_msg()
        transform.header.frame_id = self._map_frame
        transform.child_frame_id = self._odom_frame
        transform.transform.translation.x = corrected.translation[0]
        transform.transform.translation.y = corrected.translation[1]
        transform.transform.translation.z = corrected.translation[2]
        transform.transform.rotation.x = corrected.rotation.x
        transform.transform.rotation.y = corrected.rotation.y
        transform.transform.rotation.z = corrected.rotation.z
        transform.transform.rotation.w = corrected.rotation.w
        self._broadcaster.sendTransform(transform)


def main() -> None:
    rclpy.init()
    node = SlamAdapter()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
