"""Republishes gz-sim's depth cloud the way Gazebo Classic publishes it.

The two simulators publish the same sensor in different conventions, and
the difference is invisible until something reads the cloud:

- Gazebo Classic's camera plugin publishes the depth cloud in the
  **optical** convention -- z forward, x right, y down -- under the
  camera link's name;
- gz-sim publishes it in the **sensor's** convention -- x forward, y left,
  z up -- under the sensor's own name in the simulator's frame naming.

A detector that reads the cloud and asks "is the tool in there?" answers
the question in one convention and gets the answer for the other: the
tool is three quarters of a metre away from where the cloud says it is,
and the tool is never visible. Nothing fails; the detector simply reports
that the tool is not there, forever.

So the cloud is put into the convention the rest of the stack expects
here, and the stack does not change. The frame name is the camera link's
name, which is a frame the transform tree actually has: the name the
simulator gives its frames is not one.
"""

from __future__ import annotations

import numpy as np
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2

#: The ROS point-field types, and the numpy types that read them.
_FIELD_TYPES = {
    1: "<i1",
    2: "<u1",
    3: "<i2",
    4: "<u2",
    5: "<i4",
    6: "<u4",
    7: "<f4",
    8: "<f8",
}


class GzCloudConverter(Node):
    """The depth cloud, in the convention the stack reads it in."""

    def __init__(self) -> None:
        super().__init__("gz_cloud_converter")
        self.declare_parameter("input_topic", "/gz/depth/points")
        self.declare_parameter("output_topic", "/camera/depth/points")
        self.declare_parameter("frame_id", "camera_link")

        self._publisher = self.create_publisher(
            PointCloud2, str(self.get_parameter("output_topic").value), 10
        )
        self.create_subscription(
            PointCloud2,
            str(self.get_parameter("input_topic").value),
            self._on_cloud,
            5,
        )
        self.get_logger().info(
            "putting the depth cloud into the optical convention on "
            f"[{self.get_parameter('output_topic').value}]"
        )

    def _on_cloud(self, msg: PointCloud2) -> None:
        names = [field.name for field in msg.fields]
        if not {"x", "y", "z"} <= set(names):
            self.get_logger().warn("the cloud has no xyz fields; passing it on")
            self._publisher.publish(msg)
            return

        # The raw buffer, read as the fields it holds: every field is kept
        # as it is, and only the three axes are moved.
        dtype = np.dtype(
            {
                "names": names,
                "formats": [_FIELD_TYPES.get(field.datatype, "<f4") for field in msg.fields],
                "offsets": [field.offset for field in msg.fields],
                "itemsize": msg.point_step,
            }
        )
        try:
            points = np.frombuffer(msg.data, dtype=dtype).copy()
        except (TypeError, ValueError) as error:
            self.get_logger().warn(f"could not read the cloud: {error}")
            return

        # The sensor's convention (x forward, y left, z up) into the
        # optical one (z forward, x right, y down).
        x = points["x"].copy()
        y = points["y"].copy()
        z = points["z"].copy()
        points["x"] = -y
        points["y"] = -z
        points["z"] = x

        out = PointCloud2()
        out.header = msg.header
        out.header.frame_id = str(self.get_parameter("frame_id").value)
        out.height = msg.height
        out.width = msg.width
        out.is_bigendian = msg.is_bigendian
        out.is_dense = msg.is_dense
        out.fields = list(msg.fields)
        out.point_step = msg.point_step
        out.row_step = msg.row_step
        out.data = points.tobytes()
        self._publisher.publish(out)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = GzCloudConverter()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
