import math

import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2
from sensor_msgs_py import point_cloud2
from tf2_ros import Buffer, TransformException, TransformListener

from robot_collab_interfaces.msg import ToolDetection


class ToolDetectorNode(Node):
    """Publishes tool detections, and looks at the world before it does.

    Replace this node with an OpenCV/YOLO detector that publishes the same
    `ToolDetection` message to keep downstream nodes unchanged.

    The stub used to publish one fixed detection, whatever the world looked
    like. That made a visual occlusion impossible to produce: an obstacle
    between the camera and the tool changed nothing, so the question "is a
    blocked detection cleared by changing viewpoint?" had no answer on this
    stack, and the calibration said so instead of measuring it.

    So the detector looks. It knows where its tool is, it knows where the
    camera is, and it reads the depth camera's point cloud to find out
    whether the tool is visible from here. Something closer than the tool
    along the same line of sight means the tool is hidden, and a hidden tool
    is published as the low-confidence detection a real detector would
    publish for it.

    Moving the base changes the camera's place in the world, which changes
    what is in the way. That is the viewpoint change the probe measures.
    """

    def __init__(self) -> None:
        super().__init__("tool_detector_node")
        self.declare_parameter("tool_id", "hex_key_3mm")
        self.declare_parameter("tool_label", "3mm hex key")
        self.declare_parameter("source_frame", "tool_camera_link")
        self.declare_parameter("publish_hz", 1.0)
        self.declare_parameter("confidence", 0.88)
        # Where the tool is, in the world frame. A detector is configured with
        # what it is looking for; this stub is configured with where its tool
        # is, so that it can look at the right piece of the world.
        self.declare_parameter("tool_position", "1.25 -0.75 0.30")
        self.declare_parameter("world_frame", "map")
        # The confidence published when the tool cannot be seen.
        self.declare_parameter("occluded_confidence", 0.22)
        # How much closer than the tool a surface has to be to count as
        # blocking it. A surface a couple of centimetres in front of the tool
        # is the tool's own case, not an obstruction.
        self.declare_parameter("occlusion_margin", 0.08)
        # How far around the tool a surface may be and still block it.
        self.declare_parameter("occlusion_radius", 0.12)
        # The depth camera's point cloud.
        self.declare_parameter("cloud_topic", "/camera/depth/points")
        self.declare_parameter("occlusion_check", True)

        self._pub = self.create_publisher(ToolDetection, "/perception/tool_detections", 10)
        self._cloud: PointCloud2 | None = None
        self._cloud_frame = ""
        self._points: np.ndarray | None = None
        self._occluded = False
        self._occlusion_checked = False
        self._cloud_sub = self.create_subscription(
            PointCloud2,
            str(self.get_parameter("cloud_topic").value),
            self._on_cloud,
            5,
        )
        self._tf_buffer = Buffer(cache_time=rclpy.duration.Duration(seconds=10.0))
        self._tf_listener = TransformListener(self._tf_buffer, self)

        publish_hz = max(0.1, float(self.get_parameter("publish_hz").value))
        self._timer = self.create_timer(1.0 / publish_hz, self._publish_detection)
        self.get_logger().info("Tool detector stub is publishing detections.")

    # ----------------------------------------------------------------- inputs

    def _on_cloud(self, msg: PointCloud2) -> None:
        self._cloud = msg
        self._cloud_frame = str(msg.header.frame_id)
        try:
            self._points = point_cloud2.read_points_numpy(
                msg, field_names=("x", "y", "z"), skip_nans=True
            )
        except Exception as error:  # noqa: BLE001 - a bad frame is not fatal
            self.get_logger().warn(f"Could not read the depth cloud: {error}")
            self._points = None

    def _tool_position(self) -> tuple[float, float, float]:
        parts = str(self.get_parameter("tool_position").value).split()
        if len(parts) != 3:
            return (0.0, 0.0, 0.0)
        return (float(parts[0]), float(parts[1]), float(parts[2]))

    def _world_to_camera(self) -> tuple[tuple[float, float, float], tuple[float, float, float, float]] | None:
        """The transform that puts a world point into the camera's frame.

        The frame is the one the depth cloud is published in, so the lookup
        works whatever convention the camera publishes in.
        """
        frame = self._cloud_frame or str(self.get_parameter("source_frame").value)
        try:
            transform = self._tf_buffer.lookup_transform(
                frame, str(self.get_parameter("world_frame").value), rclpy.time.Time()
            )
        except (TransformException, LookupError):
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        return (
            (translation.x, translation.y, translation.z),
            (rotation.w, rotation.x, rotation.y, rotation.z),
        )

    def _camera_pose(self) -> tuple[float, float, float, float] | None:
        """The camera's place in the world: x, y, z and the yaw."""
        frame = self._cloud_frame or str(self.get_parameter("source_frame").value)
        try:
            transform = self._tf_buffer.lookup_transform(
                str(self.get_parameter("world_frame").value), frame, rclpy.time.Time()
            )
        except (TransformException, LookupError):
            return None
        translation = transform.transform.translation
        rotation = transform.transform.rotation
        yaw = math.atan2(
            2.0 * (rotation.w * rotation.z + rotation.x * rotation.y),
            1.0 - 2.0 * (rotation.y * rotation.y + rotation.z * rotation.z),
        )
        return (translation.x, translation.y, translation.z, yaw)

    # --------------------------------------------------------------- occlusion

    def _update_occlusion(self) -> None:
        """Is the tool hidden from where the camera is?

        The depth cloud is the world's own account of what is in front of
        the camera. The question is whether anything stands between the
        camera and the tool, so the answer is read off the *ray* from the
        camera to the tool: a surface close to that ray and well in front
        of the tool is something in the way.
        """
        if not bool(self.get_parameter("occlusion_check").value):
            self._occluded = False
            self._occlusion_checked = True
            return
        if self._points is None:
            return
        world_to_camera = self._world_to_camera()
        if world_to_camera is None:
            return
        translation, rotation = world_to_camera
        tool = self._tool_position()
        local = _rotate_by_quaternion(
            tool[0] - translation[0],
            tool[1] - translation[1],
            tool[2] - translation[2],
            rotation,
        )
        expected = math.sqrt(local[0] ** 2 + local[1] ** 2 + local[2] ** 2)
        if expected <= 1e-6:
            return

        radius = float(self.get_parameter("occlusion_radius").value)
        margin = float(self.get_parameter("occlusion_margin").value)
        self._occluded = self._something_in_the_way(local, expected, radius, margin)
        self._occlusion_checked = True

    def _something_in_the_way(
        self,
        tool_local: tuple[float, float, float],
        expected: float,
        radius: float,
        margin: float,
    ) -> bool:
        """Is there a surface on the ray to the tool, in front of it?

        The camera is the origin of the cloud's frame, so the ray to the
        tool is the segment from the origin to the tool's place in that
        frame. A surface within `radius` of that ray, and at least `margin`
        nearer than the tool, is what stands in the way.
        """
        points = self._points
        direction = np.asarray(tool_local, dtype=float) / expected
        coordinates = _xyz(points)
        along = coordinates @ direction
        perpendicular = np.linalg.norm(
            coordinates - np.outer(along, direction), axis=1
        )
        in_front = (along > 0.05) & (along < expected - margin)
        close = perpendicular < radius
        return bool((in_front & close).any())

    # ------------------------------------------------------------- publishing

    def _publish_detection(self) -> None:
        self._update_occlusion()
        frame = str(self.get_parameter("source_frame").value)
        camera = self._camera_pose()
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = frame
        if camera is not None:
            tool = self._tool_position()
            dx = tool[0] - camera[0]
            dy = tool[1] - camera[1]
            # The tool in the camera's frame, with the camera looking along
            # its own x axis.
            cos_yaw = math.cos(-camera[3])
            sin_yaw = math.sin(-camera[3])
            pose.pose.position.x = dx * cos_yaw - dy * sin_yaw
            pose.pose.position.y = dx * sin_yaw + dy * cos_yaw
            pose.pose.position.z = tool[2] - camera[2]
        else:
            pose.pose.position.x = 0.42
            pose.pose.position.y = -0.12
            pose.pose.position.z = 0.08
        pose.pose.orientation.w = 1.0

        occluded = self._occluded and self._occlusion_checked
        confidence = (
            float(self.get_parameter("occluded_confidence").value)
            if occluded
            else float(self.get_parameter("confidence").value)
        )

        msg = ToolDetection()
        msg.header = pose.header
        msg.tool_id = str(self.get_parameter("tool_id").value)
        msg.label = str(self.get_parameter("tool_label").value)
        msg.confidence = confidence
        msg.tool_pose = pose
        msg.source_frame = frame
        msg.detector_id = self.get_name()
        self._pub.publish(msg)
        if occluded:
            self.get_logger().warn(
                f"Tool {msg.tool_id} is not visible from here; confidence {confidence:.2f}."
            )


def _rotate_by_quaternion(
    x: float, y: float, z: float, quaternion: tuple[float, float, float, float]
) -> tuple[float, float, float]:
    """Rotate a vector by a quaternion given as (w, x, y, z)."""
    w, qx, qy, qz = quaternion
    tx = 2.0 * (qy * z - qz * y)
    ty = 2.0 * (qz * x - qx * z)
    tz = 2.0 * (qx * y - qy * x)
    return (
        x + w * tx + (qy * tz - qz * ty),
        y + w * ty + (qz * tx - qx * tz),
        z + w * tz + (qx * ty - qy * tx),
    )


def _xyz(points: np.ndarray) -> np.ndarray:
    """The cloud's points as three columns.

    The cloud arrives either as a structured array with named fields or as
    three plain columns, depending on the sensor_msgs_py version.
    """
    if getattr(points, "dtype", None) is not None and points.dtype.names:
        return np.column_stack(
            [points["x"], points["y"], points["z"]]
        ).astype(float)
    array = np.asarray(points, dtype=float)
    if array.ndim != 2 or array.shape[1] < 3:
        return np.zeros((0, 3))
    return array[:, 0:3]


def _nearest_distance(
    points: np.ndarray, target: tuple[float, float, float], radius: float
) -> float | None:
    """How far the nearest surface in the cloud is from a point.

    The cloud is in metres; the point is in the camera's frame.
    """
    if points is None or len(points) == 0:
        return None
    coordinates = _xyz(points)
    if coordinates.size == 0:
        return None
    distance_squared = np.sum(
        (coordinates - np.asarray(target, dtype=float)) ** 2, axis=1
    )
    inside = distance_squared <= radius * radius
    if not bool(inside.any()):
        return None
    return float(np.sqrt(distance_squared[inside]).min())


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ToolDetectorNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
