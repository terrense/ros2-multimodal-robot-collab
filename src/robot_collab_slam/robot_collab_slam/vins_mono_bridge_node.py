import rclpy
from geometry_msgs.msg import PoseStamped, TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from std_msgs.msg import String
from tf2_ros import TransformBroadcaster


class VinsMonoBridgeNode(Node):
    """Bridge VINS-Mono odometry into ROS2 localization topics.

    VINS-Mono is originally a ROS1 package. This adapter accepts odometry from
    a ROS2 port or ros1_bridge and republishes a stable pose topic for the
    mobile robot navigation stack.
    """

    def __init__(self) -> None:
        super().__init__("vins_mono_bridge_node")
        self.declare_parameter("vins_odometry_topic", "/vins_estimator/odometry")
        self.declare_parameter("pose_topic", "/slam/vins_pose")
        self.declare_parameter("status_topic", "/slam/vins_status")
        self.declare_parameter("publish_tf", True)
        self.declare_parameter("map_frame", "map")
        self.declare_parameter("base_frame", "base_link")
        self._pose_pub = self.create_publisher(
            PoseStamped,
            str(self.get_parameter("pose_topic").value),
            10,
        )
        self._status_pub = self.create_publisher(
            String,
            str(self.get_parameter("status_topic").value),
            10,
        )
        self._tf = TransformBroadcaster(self)
        self._odom_sub = self.create_subscription(
            Odometry,
            str(self.get_parameter("vins_odometry_topic").value),
            self._handle_odometry,
            20,
        )
        self.get_logger().info("VINS-Mono ROS2 bridge is ready.")

    def _handle_odometry(self, msg: Odometry) -> None:
        pose = PoseStamped()
        pose.header = msg.header
        if not pose.header.frame_id:
            pose.header.frame_id = str(self.get_parameter("map_frame").value)
        pose.pose = msg.pose.pose
        self._pose_pub.publish(pose)

        if bool(self.get_parameter("publish_tf").value):
            self._publish_tf(pose)

        status = String()
        status.data = "vins_mono_tracking"
        self._status_pub.publish(status)

    def _publish_tf(self, pose: PoseStamped) -> None:
        transform = TransformStamped()
        transform.header = pose.header
        transform.child_frame_id = str(self.get_parameter("base_frame").value)
        transform.transform.translation.x = pose.pose.position.x
        transform.transform.translation.y = pose.pose.position.y
        transform.transform.translation.z = pose.pose.position.z
        transform.transform.rotation = pose.pose.orientation
        self._tf.sendTransform(transform)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = VinsMonoBridgeNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

