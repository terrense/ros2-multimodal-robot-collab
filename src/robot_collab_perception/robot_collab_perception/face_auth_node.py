import asyncio

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node

from robot_collab_interfaces.action import VerifyOperator
from robot_collab_interfaces.msg import IdentityStatus


class FaceAuthNode(Node):
    """Identity verification action server.

    The stub checks an allow-list. A production implementation can compare face
    embeddings, badge ids, or a fused identity signal before returning the same
    action result.
    """

    def __init__(self) -> None:
        super().__init__("face_auth_node")
        self.declare_parameter("authorized_operators", ["operator_001", "operator_002"])
        self.declare_parameter("simulate_step_seconds", 0.4)
        self._status_pub = self.create_publisher(IdentityStatus, "/perception/identity_status", 10)
        self._server = ActionServer(
            self,
            VerifyOperator,
            "/skills/verify_operator",
            execute_callback=self._execute_verify,
            goal_callback=self._handle_goal,
            cancel_callback=self._handle_cancel,
        )
        self.get_logger().info("Face authorization stub is ready.")

    def _handle_goal(self, goal_request: VerifyOperator.Goal) -> GoalResponse:
        if not goal_request.operator_id:
            self.get_logger().warn("Rejected identity verification goal without operator_id.")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _handle_cancel(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    async def _execute_verify(self, goal_handle) -> VerifyOperator.Result:
        goal = goal_handle.request
        steps = [
            ("CAPTURE_FACE", 0.25, "Capturing face frame."),
            ("MATCH_IDENTITY", 0.70, "Comparing operator embedding."),
            ("AUTHORIZE", 1.00, "Finalizing authorization."),
        ]
        for state, progress, detail in steps:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return self._result(False, 0.0, "Identity verification canceled.")
            feedback = VerifyOperator.Feedback()
            feedback.state = state
            feedback.progress = progress
            feedback.detail = detail
            goal_handle.publish_feedback(feedback)
            await asyncio.sleep(float(self.get_parameter("simulate_step_seconds").value))

        authorized = goal.operator_id in set(self.get_parameter("authorized_operators").value)
        confidence = 0.93 if authorized else 0.31
        message = "Operator authorized." if authorized else "Operator is not authorized."
        self._publish_identity(goal.operator_id, authorized, confidence, message)
        if authorized:
            goal_handle.succeed()
        else:
            goal_handle.abort()
        return self._result(authorized, confidence, message)

    def _publish_identity(self, operator_id: str, authorized: bool, confidence: float, reason: str) -> None:
        msg = IdentityStatus()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.operator_id = operator_id
        msg.authorized = authorized
        msg.confidence = confidence
        msg.reason = reason
        self._status_pub.publish(msg)

    @staticmethod
    def _result(authorized: bool, confidence: float, message: str) -> VerifyOperator.Result:
        result = VerifyOperator.Result()
        result.authorized = authorized
        result.confidence = confidence
        result.message = message
        return result


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FaceAuthNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

