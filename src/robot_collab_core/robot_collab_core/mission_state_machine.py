import uuid

import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node
from rclpy.task import Future

from robot_collab_interfaces.action import DeliverTool, NavigateToStation, PickAndPlace, VerifyOperator
from robot_collab_interfaces.msg import MissionEvent, ToolDetection
from robot_collab_interfaces.srv import QuerySystemState


def _async_sleep(node: Node, duration: float) -> Future:
    """Awaitable sleep driven by a ROS2 timer.

    rclpy's coroutine executor does not run a real asyncio event loop, so
    plain ``asyncio.sleep`` raises "no running event loop" inside action
    server callbacks. A timer-backed Future integrates with rclpy's own
    awaiting mechanism instead.
    """
    future = Future()

    def _on_timer() -> None:
        timer.cancel()
        timer.destroy()
        if not future.done():
            future.set_result(None)

    timer = node.create_timer(max(duration, 0.0001), _on_timer)
    return future


class MissionStateMachine(Node):
    """Task-level orchestrator for the tool delivery workflow."""

    def __init__(self) -> None:
        super().__init__("mission_state_machine")
        self.declare_parameter("simulate_step_seconds", 0.8)
        self.declare_parameter("motion_enabled", True)
        self.declare_parameter("max_retries", 1)
        self.declare_parameter("pickup_station", "tool_shelf")
        self.declare_parameter("handover_frame", "handover_zone")
        self.declare_parameter("tool_detection_timeout_sec", 3.0)
        self.declare_parameter("min_tool_confidence", 0.70)

        self._mission_id = ""
        self._state = "IDLE"
        self._warnings: list[str] = []
        self._latest_tools: dict[str, ToolDetection] = {}
        self._event_pub = self.create_publisher(MissionEvent, "/mission/events", 10)
        self._tool_sub = self.create_subscription(
            ToolDetection,
            "/perception/tool_detections",
            self._handle_tool_detection,
            10,
        )
        self._state_srv = self.create_service(
            QuerySystemState,
            "/system/query_state",
            self._handle_query_state,
        )
        self._verify_client = ActionClient(self, VerifyOperator, "/skills/verify_operator")
        self._nav_client = ActionClient(self, NavigateToStation, "/skills/navigate_to_station")
        self._arm_client = ActionClient(self, PickAndPlace, "/skills/pick_and_place")
        self._deliver_action = ActionServer(
            self,
            DeliverTool,
            "/mission/deliver_tool",
            execute_callback=self._execute_delivery,
            goal_callback=self._handle_goal,
            cancel_callback=self._handle_cancel,
        )
        self.get_logger().info("Mission state machine is ready.")

    def _handle_tool_detection(self, msg: ToolDetection) -> None:
        self._latest_tools[msg.tool_id] = msg

    def _handle_goal(self, goal_request: DeliverTool.Goal) -> GoalResponse:
        if not goal_request.tool_id or not goal_request.target_station:
            self.get_logger().warn("Rejected mission goal with missing tool or station.")
            return GoalResponse.REJECT
        if not self.get_parameter("motion_enabled").value:
            self.get_logger().warn("Rejected mission goal because motion is disabled.")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _handle_cancel(self, _goal_handle) -> CancelResponse:
        self.get_logger().info("Cancel requested for active delivery mission.")
        return CancelResponse.ACCEPT

    def _handle_query_state(self, request: QuerySystemState.Request, response: QuerySystemState.Response):
        del request
        response.mission_id = self._mission_id
        response.state = self._state
        response.active_nodes = [
            "mission_state_machine",
            "nav_skill_server",
            "arm_skill_server",
            "tool_detector_node",
            "face_auth_node",
            "voice_gateway_node",
            "agent_gateway_node",
        ]
        response.warnings = self._warnings
        response.motion_enabled = bool(self.get_parameter("motion_enabled").value)
        return response

    async def _execute_delivery(self, goal_handle) -> DeliverTool.Result:
        goal = goal_handle.request
        self._mission_id = f"mission-{uuid.uuid4().hex[:8]}"
        self._warnings = []
        self.get_logger().info(
            f"Starting delivery mission {self._mission_id}: "
            f"tool={goal.tool_id} station={goal.target_station} operator={goal.operator_id}"
        )

        if goal_handle.is_cancel_requested:
            return self._cancel(goal_handle)

        authorized = await self._verify_operator(goal, goal_handle)
        if not authorized:
            goal_handle.abort()
            return self._result(False, "Operator authorization failed.")

        if goal.require_confirmation:
            await self._simulated_step(
                goal_handle,
                "WAIT_FOR_CONFIRMATION",
                0.18,
                "Delivery confirmation accepted.",
            )

        tool_detection = await self._find_tool(goal.tool_id, goal_handle)
        if tool_detection is None:
            goal_handle.abort()
            return self._result(False, f"Could not find tool {goal.tool_id}.")

        pickup_station = str(self.get_parameter("pickup_station").value)
        if not await self._navigate(pickup_station, "pickup requested tool", 0.40, 0.60, goal_handle):
            goal_handle.abort()
            return self._result(False, f"Navigation to {pickup_station} failed.")

        if not await self._pick_and_place(goal.tool_id, tool_detection, goal_handle):
            goal_handle.abort()
            return self._result(False, f"Manipulation failed for {goal.tool_id}.")

        if not await self._navigate(goal.target_station, "deliver requested tool", 0.82, 0.94, goal_handle):
            goal_handle.abort()
            return self._result(False, f"Navigation to {goal.target_station} failed.")

        await self._simulated_step(
            goal_handle,
            "PLACE_AND_HANDOVER",
            0.98,
            "Tool placed in the handover area.",
        )

        self._publish_state("COMPLETED", 1.0, "Tool delivered and handover confirmed.")
        goal_handle.succeed()
        return self._result(True, "Tool delivery completed.")

    async def _verify_operator(self, goal: DeliverTool.Goal, goal_handle) -> bool:
        self._publish_feedback(goal_handle, "VERIFY_IDENTITY", 0.08, f"Verifying operator {goal.operator_id}.")
        if not self._verify_client.wait_for_server(timeout_sec=2.0):
            self._warnings.append("verify_operator action server unavailable")
            return False
        verify_goal = VerifyOperator.Goal()
        verify_goal.operator_id = goal.operator_id
        verify_goal.require_face_match = True
        action_goal = await self._verify_client.send_goal_async(
            verify_goal,
            feedback_callback=lambda msg: self._relay_feedback(
                goal_handle,
                "VERIFY_IDENTITY",
                0.08,
                0.16,
                msg.feedback,
            ),
        )
        if not action_goal.accepted:
            return False
        result = await action_goal.get_result_async()
        return bool(result.result.authorized)

    async def _find_tool(self, tool_id: str, goal_handle) -> ToolDetection | None:
        self._publish_feedback(goal_handle, "DETECT_TOOL", 0.22, f"Searching for tool {tool_id}.")
        timeout = float(self.get_parameter("tool_detection_timeout_sec").value)
        min_conf = float(self.get_parameter("min_tool_confidence").value)
        deadline = self.get_clock().now().nanoseconds + int(timeout * 1_000_000_000)
        while self.get_clock().now().nanoseconds < deadline:
            detection = self._latest_tools.get(tool_id)
            if detection is not None and detection.confidence >= min_conf:
                detail = f"Found {tool_id} with confidence {detection.confidence:.2f}."
                self._publish_feedback(goal_handle, "DETECT_TOOL", 0.32, detail)
                return detection
            await _async_sleep(self, 0.2)
        self._warnings.append(f"tool detection timeout for {tool_id}")
        return None

    async def _navigate(
        self,
        station_id: str,
        reason: str,
        progress_start: float,
        progress_end: float,
        goal_handle,
    ) -> bool:
        self._publish_feedback(goal_handle, "PLAN_PATH", progress_start, f"Planning route to {station_id}.")
        if not self._nav_client.wait_for_server(timeout_sec=2.0):
            self._warnings.append("navigate_to_station action server unavailable")
            return False
        nav_goal = NavigateToStation.Goal()
        nav_goal.station_id = station_id
        nav_goal.reason = reason
        action_goal = await self._nav_client.send_goal_async(
            nav_goal,
            feedback_callback=lambda msg: self._relay_feedback(
                goal_handle,
                "NAVIGATE",
                progress_start,
                progress_end,
                msg.feedback,
            ),
        )
        if not action_goal.accepted:
            return False
        result = await action_goal.get_result_async()
        return bool(result.result.success)

    async def _pick_and_place(self, tool_id: str, detection: ToolDetection, goal_handle) -> bool:
        self._publish_feedback(goal_handle, "PICK_TOOL", 0.64, f"Preparing arm for {tool_id}.")
        if not self._arm_client.wait_for_server(timeout_sec=2.0):
            self._warnings.append("pick_and_place action server unavailable")
            return False
        arm_goal = PickAndPlace.Goal()
        arm_goal.tool_id = tool_id
        arm_goal.source_frame = detection.source_frame
        arm_goal.target_frame = str(self.get_parameter("handover_frame").value)
        arm_goal.tool_pose = detection.tool_pose if detection.tool_pose else PoseStamped()
        action_goal = await self._arm_client.send_goal_async(
            arm_goal,
            feedback_callback=lambda msg: self._relay_feedback(
                goal_handle,
                "PICK_TOOL",
                0.64,
                0.80,
                msg.feedback,
            ),
        )
        if not action_goal.accepted:
            return False
        result = await action_goal.get_result_async()
        return bool(result.result.success)

    async def _simulated_step(self, goal_handle, state: str, progress: float, detail: str) -> None:
        self._publish_feedback(goal_handle, state, progress, detail)
        await _async_sleep(self, float(self.get_parameter("simulate_step_seconds").value))

    def _relay_feedback(self, goal_handle, state: str, start: float, end: float, feedback) -> None:
        progress = start + (end - start) * float(feedback.progress)
        detail = feedback.detail or state
        self._publish_feedback(goal_handle, state, progress, detail)

    def _publish_feedback(self, goal_handle, state: str, progress: float, detail: str) -> None:
        if goal_handle.is_cancel_requested:
            return
        self._publish_state(state, progress, detail)
        feedback = DeliverTool.Feedback()
        feedback.state = state
        feedback.progress = progress
        feedback.detail = detail
        goal_handle.publish_feedback(feedback)

    def _cancel(self, goal_handle) -> DeliverTool.Result:
        self._publish_state("CANCELED", 0.0, "Mission canceled by operator.")
        goal_handle.canceled()
        return self._result(False, "Mission canceled.")

    def _publish_state(self, state: str, progress: float, detail: str) -> None:
        self._state = state
        event = MissionEvent()
        event.header.stamp = self.get_clock().now().to_msg()
        event.mission_id = self._mission_id
        event.state = state
        event.progress = progress
        event.detail = detail
        event.code = 0
        self._event_pub.publish(event)
        self.get_logger().info(f"[{state}] {progress * 100.0:.0f}% {detail}")

    def _result(self, success: bool, message: str) -> DeliverTool.Result:
        result = DeliverTool.Result()
        result.success = success
        result.mission_id = self._mission_id
        result.message = message
        return result


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MissionStateMachine()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
