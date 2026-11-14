import math

import numpy as np
import rclpy
from gazebo_msgs.msg import LinkStates, ModelStates
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
    qos_profile_sensor_data,
)
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
        # blocking it. A surface a couple of centimetres in front of the
        # tool is the tool's own case, not an obstruction.
        self.declare_parameter("occlusion_margin", 0.25)
        # How far around the tool a surface may be and still block it.
        self.declare_parameter("occlusion_radius", 0.12)
        # How close a surface has to be to the tool's place in the cloud for
        # the tool to count as seen.
        self.declare_parameter("visibility_radius", 0.25)
        # The depth camera's point cloud.
        self.declare_parameter("cloud_topic", "/camera/depth/points")
        self.declare_parameter("camera_link", "camera_link")
        # Where the camera sits on the robot, as "x y z yaw" in the robot's
        # root frame. The launch reads it out of the robot description.
        self.declare_parameter("camera_mount", "")
        self.declare_parameter("occlusion_check", True)

        self._pub = self.create_publisher(ToolDetection, "/perception/tool_detections", 10)
        self._cloud: PointCloud2 | None = None
        self._cloud_frame = ""
        self._points: np.ndarray | None = None
        self._truth: tuple[float, float, float, float] | None = None
        self._base_pose: np.ndarray | None = None
        self._base_rotation: np.ndarray | None = None
        self._camera_link: tuple[float, float, float, float] | None = None
        self._mount_cache: tuple[float, tuple[float, float, float]] | None = None
        self._robot_model = "collab_mobile_base"
        self._occluded = False
        self._visible = False
        self._occlusion_checked = False
        self._logged_occlusion = False
        self._reported: set[str] = set()
        self._last_report: tuple[bool, bool] | None = None
        self._heartbeat = 0
        self._model_states_sub = self.create_subscription(
            ModelStates,
            "/model_states",
            self._on_model_states,
            qos_profile_sensor_data,
        )
        # The simulator's own account of where the camera is. The static
        # transforms carry the camera's mount on the chassis, but they are
        # published once, at startup, and a subscriber that joins later does
        # not always get them: the detector then has no camera at all, and
        # reports a tool it cannot see as a tool that is not there. The link
        # states are published all the time, and they are the truth.
        self._link_states_sub = self.create_subscription(
            LinkStates,
            "/link_states",
            self._on_link_states,
            qos_profile_sensor_data,
        )
        self._cloud_sub = self.create_subscription(
            PointCloud2,
            str(self.get_parameter("cloud_topic").value),
            self._on_cloud,
            # The default profile, not the sensor-data one: the camera's
            # publisher is reliable, and a best-effort reader does not get
            # everything from it here.
            5,
        )
        self._tf_buffer = Buffer(cache_time=rclpy.duration.Duration(seconds=10.0))
        # The static transforms (the camera's mount, the map anchor) are
        # published once, with transient-local durability. A listener that
        # subscribes volatile does not get them at all, and the camera's
        # frame simply does not exist -- which is how the detector ended up
        # publishing confident detections for a tool it could not see.
        self._tf_listener = TransformListener(
            self._tf_buffer,
            self,
            static_qos=QoSProfile(
                depth=1,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
                reliability=QoSReliabilityPolicy.RELIABLE,
            ),
        )

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

    def _on_model_states(self, msg) -> None:
        """Where the chassis really is, and how it is turned.

        The simulator publishes the poses of everything in the world. The
        odometry is the robot's own account of where it is, and in this
        simulation the wheels slip: the odometry's heading says one thing
        and the chassis is somewhere else. A detector that trusted the
        odometry would look in the wrong direction.

        The whole attitude is kept, not just the heading. The base does not
        always sit level: the wheel velocities are imposed every step, so a
        base that is tilted once stays tilted, and the camera on a tilted
        base looks somewhere else entirely.
        """
        for name, pose in zip(msg.name, msg.pose, strict=False):
            if name != self._robot_model:
                continue
            orientation = pose.orientation
            yaw = math.atan2(
                2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
                1.0 - 2.0 * (orientation.y**2 + orientation.z**2),
            )
            self._truth = (
                float(pose.position.x),
                float(pose.position.y),
                float(pose.position.z),
                yaw,
            )
            self._base_pose = np.array(
                [pose.position.x, pose.position.y, pose.position.z], dtype=float
            )
            self._base_rotation = _quaternion_matrix(
                orientation.w, orientation.x, orientation.y, orientation.z
            )

    def _on_link_states(self, msg) -> None:
        """Where the camera is, according to the simulator.

        The camera's place in the world, with its heading. This is the
        detector's own account of where it is looking, and it needs no
        composition of transforms and no assumption about which way a
        frame is expressed in.
        """
        for name, pose in zip(msg.name, msg.pose, strict=False):
            if name != str(self.get_parameter("camera_link").value):
                continue
            orientation = pose.orientation
            yaw = math.atan2(
                2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
                1.0 - 2.0 * (orientation.y**2 + orientation.z**2),
            )
            self._camera_link = (
                float(pose.position.x),
                float(pose.position.y),
                float(pose.position.z),
                yaw,
            )

    def _report_once(self, message: str) -> None:
        """Say what the detector is waiting for, once per distinct reason."""
        if message in self._reported:
            return
        self._reported.add(message)
        self.get_logger().warn(f"[OCCLUSION] {message}")

    def _visibility_radius(self) -> float:
        """How close a surface has to be to the tool for the tool to be seen."""
        return float(self.get_parameter("visibility_radius").value)

    def _camera_transform(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Where the camera is, and which way it looks.

        The camera's place in the world and the rotation that takes the
        camera's own frame into the world's. The chassis's attitude is
        used in full: a base that is tilted looks somewhere else, and a
        detector that assumes a level base looks in the wrong place.
        """
        if self._base_pose is None or self._base_rotation is None:
            return None
        mount = self._mount()
        if mount is None:
            return None
        yaw, offset = mount
        mount_rotation = np.array(
            [
                [math.cos(yaw), -math.sin(yaw), 0.0],
                [math.sin(yaw), math.cos(yaw), 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=float,
        )
        rotation = self._base_rotation @ mount_rotation
        position = self._base_pose + self._base_rotation @ np.asarray(offset, dtype=float)
        return position, rotation

    def _camera_pose(self) -> tuple[tuple[float, float, float], tuple[float, float, float, float]] | None:
        """Where the camera is, and which way it looks.

        The simulator says where the camera is. When it does not -- a robot
        that is not in a simulator, a simulator that does not publish link
        states -- the camera's place is worked out from the chassis's place
        and the camera's mount on it, which is what the static transforms
        are for.
        """
        if self._camera_link is not None:
            x, y, z, yaw = self._camera_link
            return (x, y, z), (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
        if self._truth is None:
            return None
        camera = self._mount()
        if camera is None:
            return None
        rotation, offset = camera
        cos_yaw = math.cos(self._truth[3])
        sin_yaw = math.sin(self._truth[3])
        world = (
            self._truth[0] + offset[0] * cos_yaw - offset[1] * sin_yaw,
            self._truth[1] + offset[0] * sin_yaw + offset[1] * cos_yaw,
            self._truth[2] + offset[2],
        )
        yaw = self._truth[3] + rotation
        return world, (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))

    def _mount(self) -> tuple[float, tuple[float, float, float]] | None:
        """The camera's place on the chassis, and its heading relative to it.

        The parameter the launch hands over, which is read out of the robot
        description: the description is the only place the mount is written
        down, and a node that has to know where the camera is has to be
        told. The static transforms carry the same information, but they
        are published once, when the robot description arrives, and a
        subscriber that joins later does not always get them.
        """
        if self._mount_cache is not None:
            return self._mount_cache
        text = str(self.get_parameter("camera_mount").value).split()
        if len(text) == 4:
            x, y, z, yaw = (float(value) for value in text)
            self._mount_cache = (yaw, (x, y, z))
            return self._mount_cache
        try:
            camera = self._tf_buffer.lookup_transform(
                "base_footprint", "camera_link", rclpy.time.Time()
            )
            base = self._tf_buffer.lookup_transform(
                "base_footprint", "base_link", rclpy.time.Time()
            )
        except (TransformException, LookupError):
            return None
        camera_rotation = _quaternion_matrix(
            camera.transform.rotation.w,
            camera.transform.rotation.x,
            camera.transform.rotation.y,
            camera.transform.rotation.z,
        )
        base_rotation = _quaternion_matrix(
            base.transform.rotation.w,
            base.transform.rotation.x,
            base.transform.rotation.y,
            base.transform.rotation.z,
        )
        offset = camera_rotation.T @ (
            np.array(
                [
                    camera.transform.translation.x,
                    camera.transform.translation.y,
                    camera.transform.translation.z,
                ],
                dtype=float,
            )
            - np.array(
                [
                    base.transform.translation.x,
                    base.transform.translation.y,
                    base.transform.translation.z,
                ],
                dtype=float,
            )
        )
        heading = math.atan2(
            camera_rotation[1, 0], camera_rotation[0, 0]
        ) - math.atan2(base_rotation[1, 0], base_rotation[0, 0])
        self._mount_cache = (heading, (float(offset[0]), float(offset[1]), float(offset[2])))
        return self._mount_cache

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
            self._report_once(
                f"no points: cloud received={self._cloud is not None}, "
                f"frame={self._cloud_frame!r}"
            )
            return
        camera = self._camera_transform()
        if camera is None:
            self._report_once(
                f"no camera pose: truth={self._truth is not None}, "
                f"mount={self._mount() is not None}"
            )
            return
        translation, rotation = camera
        tool = self._tool_position()
        # The tool in the camera's own frame. The rotation is the whole
        # attitude of the camera, not just its heading: a base that is
        # tilted looks somewhere else, and a detector that looks where a
        # level camera would look finds a tool that is not there.
        local = rotation.T @ (
            np.asarray(tool, dtype=float) - translation
        )
        expected = float(np.linalg.norm(local))
        if expected <= 1e-6:
            return
        # The depth cloud is published in the optical convention: z forward,
        # x right, y down. The pose above is the camera's link frame, so the
        # tool's place in the cloud has to be moved into the convention the
        # cloud is actually in -- otherwise the ray points somewhere the
        # camera is not looking.
        local = (-local[1], -local[2], local[0])

        radius = float(self.get_parameter("occlusion_radius").value)
        margin = float(self.get_parameter("occlusion_margin").value)
        self._occluded = self._something_in_the_way(local, expected, radius, margin)
        # Being blocked and not being there at all are the same thing to a
        # detector: the tool cannot be picked up from what the camera sees.
        # The difference matters for the experiment, not for the robot.
        nearest = _nearest_distance(self._points, local, self._visibility_radius())
        self._visible = nearest is not None
        self._occlusion_checked = True
        # Say what the detector sees whenever the answer changes. A detector
        # that quietly keeps the same answer is a detector nobody can check.
        report = (self._occluded, self._visible)
        if report != self._last_report:
            self._last_report = report
            self.get_logger().warn(
                f"[OCCLUSION] camera=({translation[0]:.2f}, {translation[1]:.2f}, "
                f"{translation[2]:.2f}) tool {expected:.2f} m away "
                f"optical=({local[0]:.2f}, {local[1]:.2f}, {local[2]:.2f}) "
                f"occluded={self._occluded} visible={self._visible} "
                f"nearest={nearest if nearest is None else round(nearest, 3)} "
                f"points={0 if self._points is None else len(self._points)}"
            )

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
        camera = self._camera_transform()
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = frame
        if camera is not None:
            translation, rotation = camera
            tool = self._tool_position()
            # The tool in the camera's own frame: x forward, y to the left,
            # z up.
            local = rotation.T @ (np.asarray(tool, dtype=float) - translation)
            pose.pose.position.x = float(local[0])
            pose.pose.position.y = float(local[1])
            pose.pose.position.z = float(local[2])
        else:
            pose.pose.position.x = 0.42
            pose.pose.position.y = -0.12
            pose.pose.position.z = 0.08
        pose.pose.orientation.w = 1.0

        # A detector that cannot see the tool says so with a low
        # confidence. What is in the way is reported as well, but it is not
        # what decides: the shelf the tool stands on is itself a surface
        # along the last of the ray, and a detector that called every
        # grazing surface an obstruction would call the tool hidden while
        # looking straight at it. The tool's own pixels are the answer.
        hidden = self._occlusion_checked and not self._visible
        confidence = (
            float(self.get_parameter("occluded_confidence").value)
            if hidden
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
        if hidden:
            self.get_logger().warn(
                f"Tool {msg.tool_id} is not visible from here; confidence {confidence:.2f}."
            )
        # A heartbeat: what the detector believes, every few seconds, whether
        # it changed or not. A detector whose account of the world cannot be
        # read off the log is a detector nobody can check.
        self._heartbeat += 1
        if self._heartbeat % 25 == 0:
            camera = self._camera_transform()
            where = (
                "no camera"
                if camera is None
                else f"camera=({camera[0][0]:.2f}, {camera[0][1]:.2f}, {camera[0][2]:.2f})"
            )
            self.get_logger().warn(
                f"[OCCLUSION] {where} checked={self._occlusion_checked} "
                f"occluded={self._occluded} visible={self._visible} "
                f"points={0 if self._points is None else len(self._points)} "
                f"confidence={confidence:.2f}"
            )


def _quaternion_matrix(
    w: float, x: float, y: float, z: float
) -> np.ndarray:
    """A rotation matrix from a quaternion."""
    return np.array(
        [
            [
                1 - 2 * (y * y + z * z),
                2 * (x * y - z * w),
                2 * (x * z + y * w),
            ],
            [
                2 * (x * y + z * w),
                1 - 2 * (x * x + z * z),
                2 * (y * z - x * w),
            ],
            [
                2 * (x * z - y * w),
                2 * (y * z + x * w),
                1 - 2 * (x * x + y * y),
            ],
        ],
        dtype=float,
    )


def _matrix_quaternion(matrix: np.ndarray) -> tuple[float, float, float, float]:
    """A quaternion (w, x, y, z) from a rotation matrix."""
    trace = matrix[0, 0] + matrix[1, 1] + matrix[2, 2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * scale
        x = (matrix[2, 1] - matrix[1, 2]) / scale
        y = (matrix[0, 2] - matrix[2, 0]) / scale
        z = (matrix[1, 0] - matrix[0, 1]) / scale
    elif matrix[0, 0] > matrix[1, 1] and matrix[0, 0] > matrix[2, 2]:
        scale = math.sqrt(1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2]) * 2.0
        w = (matrix[2, 1] - matrix[1, 2]) / scale
        x = 0.25 * scale
        y = (matrix[0, 1] + matrix[1, 0]) / scale
        z = (matrix[0, 2] + matrix[2, 0]) / scale
    elif matrix[1, 1] > matrix[2, 2]:
        scale = math.sqrt(1.0 + matrix[1, 1] - matrix[0, 0] - matrix[2, 2]) * 2.0
        w = (matrix[0, 2] - matrix[2, 0]) / scale
        x = (matrix[0, 1] + matrix[1, 0]) / scale
        y = 0.25 * scale
        z = (matrix[1, 2] + matrix[2, 1]) / scale
    else:
        scale = math.sqrt(1.0 + matrix[2, 2] - matrix[0, 0] - matrix[1, 1]) * 2.0
        w = (matrix[1, 0] - matrix[0, 1]) / scale
        x = (matrix[0, 2] + matrix[2, 0]) / scale
        y = (matrix[1, 2] + matrix[2, 1]) / scale
        z = 0.25 * scale
    return (float(w), float(x), float(y), float(z))


def _inverse_quaternion(
    quaternion: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """The inverse of a rotation given as (w, x, y, z).

    Turning a world vector into a camera-frame vector means undoing the
    camera's heading, and the undoing of a rotation is its conjugate.
    """
    w, x, y, z = quaternion
    return (w, -x, -y, -z)


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
    three plain columns, depending on the sensor_msgs_py version. The
    points the camera could not measure are NaN, and a NaN in the middle of
    the arithmetic turns the whole comparison into a "no" -- so they go
    first.
    """
    if getattr(points, "dtype", None) is not None and points.dtype.names:
        coordinates = np.column_stack(
            [points["x"], points["y"], points["z"]]
        ).astype(float)
    else:
        array = np.asarray(points, dtype=float)
        if array.ndim != 2 or array.shape[1] < 3:
            return np.zeros((0, 3))
        coordinates = array[:, 0:3]
    return coordinates[np.isfinite(coordinates).all(axis=1)]


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
