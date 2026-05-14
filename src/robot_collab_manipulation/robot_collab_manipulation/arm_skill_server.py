import asyncio

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node

from robot_collab_interfaces.action import PickAndPlace


class ArmSkillServer(Node):
    """Arm and gripper wrapper for pick/place work."""

    def __init__(self) -> None:
        super().__init__("arm_skill_server")
        self.declare_parameter("simulate_step_seconds", 0.5)
        self.declare_parameter("min_detection_confidence", 0.75)
        self._server = ActionServer(
            self,
            PickAndPlace,
            "/skills/pick_and_place",
            execute_callback=self._execute_pick_and_place,
            goal_callback=self._handle_goal,
            cancel_callback=self._handle_cancel,
        )
        self.get_logger().info("Manipulation skill server is ready.")

    def _handle_goal(self, goal_request: PickAndPlace.Goal) -> GoalResponse:
        if not goal_request.tool_id:
            self.get_logger().warn("Rejected manipulation goal without tool_id.")
            return GoalResponse.REJECT
        if not goal_request.source_frame or not goal_request.target_frame:
            self.get_logger().warn("Rejected manipulation goal without source/target frame.")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _handle_cancel(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    async def _execute_pick_and_place(self, goal_handle) -> PickAndPlace.Result:
        goal = goal_handle.request
        steps = [
            ("CHECK_SCENE", 0.12, f"Checking scene for {goal.tool_id}."),
            ("PLAN_GRASP", 0.28, "Planning pre-grasp and grasp pose."),
            ("APPROACH", 0.45, "Moving arm to pre-grasp pose."),
            ("GRASP", 0.62, "Closing gripper."),
            ("LIFT", 0.76, "Lifting tool from workspace."),
            ("PLACE", 0.92, f"Placing tool in {goal.target_frame}."),
            ("DONE", 1.00, "Pick and place complete."),
        ]
        for state, progress, detail in steps:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return self._result(False, "Manipulation canceled.")
            feedback = PickAndPlace.Feedback()
            feedback.state = state
            feedback.progress = progress
            feedback.detail = detail
            goal_handle.publish_feedback(feedback)
            self.get_logger().info(f"[{state}] {detail}")
            await asyncio.sleep(float(self.get_parameter("simulate_step_seconds").value))

        goal_handle.succeed()
        return self._result(True, f"Moved {goal.tool_id} into {goal.target_frame}.")

    @staticmethod
    def _result(success: bool, message: str) -> PickAndPlace.Result:
        result = PickAndPlace.Result()
        result.success = success
        result.message = message
        return result


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ArmSkillServer()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

