import asyncio

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node

from robot_collab_interfaces.action import NavigateToStation


class NavSkillServer(Node):
    """Station-level navigation wrapper.

    In simulation mode this server emits deterministic progress. In hardware mode,
    replace `_execute_navigation` with a Nav2 NavigateToPose action client.
    """

    def __init__(self) -> None:
        super().__init__("nav_skill_server")
        self.declare_parameter("simulate_step_seconds", 0.5)
        self.declare_parameter("known_stations", ["tool_shelf", "station_a", "station_b", "handover_zone"])
        self._server = ActionServer(
            self,
            NavigateToStation,
            "/skills/navigate_to_station",
            execute_callback=self._execute_navigation,
            goal_callback=self._handle_goal,
            cancel_callback=self._handle_cancel,
        )
        self.get_logger().info("Navigation skill server is ready.")

    def _handle_goal(self, goal_request: NavigateToStation.Goal) -> GoalResponse:
        known = set(self.get_parameter("known_stations").value)
        if goal_request.station_id not in known:
            self.get_logger().warn(f"Unknown navigation station: {goal_request.station_id}")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _handle_cancel(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    async def _execute_navigation(self, goal_handle) -> NavigateToStation.Result:
        goal = goal_handle.request
        steps = [
            ("ACCEPTED", 0.10, f"Accepted navigation request to {goal.station_id}."),
            ("LOCALIZING", 0.25, "Checking map frame and robot pose."),
            ("PLANNING", 0.40, "Planning global path."),
            ("MOVING", 0.75, "Following path with local planner."),
            ("ARRIVED", 1.00, f"Arrived at {goal.station_id}."),
        ]
        for state, progress, detail in steps:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return self._result(False, "Navigation canceled.")
            feedback = NavigateToStation.Feedback()
            feedback.state = state
            feedback.progress = progress
            feedback.detail = detail
            goal_handle.publish_feedback(feedback)
            self.get_logger().info(f"[{state}] {detail}")
            await asyncio.sleep(float(self.get_parameter("simulate_step_seconds").value))

        goal_handle.succeed()
        return self._result(True, f"Reached {goal.station_id}.")

    @staticmethod
    def _result(success: bool, message: str) -> NavigateToStation.Result:
        result = NavigateToStation.Result()
        result.success = success
        result.message = message
        return result


def main(args=None) -> None:
    rclpy.init(args=args)
    node = NavSkillServer()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

