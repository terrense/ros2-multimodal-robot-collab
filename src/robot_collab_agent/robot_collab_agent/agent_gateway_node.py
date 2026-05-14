import re
from dataclasses import dataclass

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String

from robot_collab_interfaces.action import DeliverTool
from robot_collab_interfaces.msg import GestureCommand


@dataclass(frozen=True)
class DeliveryCommand:
    tool_id: str
    target_station: str
    operator_id: str


class AgentGatewayNode(Node):
    """Routes planner or ASR text commands into ROS2 mission actions."""

    COMMAND_RE = re.compile(
        r"deliver\s+(?P<tool>\S+)\s+to\s+(?P<station>\S+)(?:\s+for\s+(?P<operator>\S+))?",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        super().__init__("agent_gateway_node")
        self.declare_parameter("default_operator_id", "operator_001")
        self.declare_parameter("require_confirmation", True)
        self._tts_pub = self.create_publisher(String, "/hri/tts_text", 10)
        self._command_sub = self.create_subscription(String, "/agent/command_text", self._handle_text_command, 10)
        self._gesture_sub = self.create_subscription(
            GestureCommand,
            "/hri/gesture_command",
            self._handle_gesture_command,
            10,
        )
        self._mission_client = ActionClient(self, DeliverTool, "/mission/deliver_tool")
        self.get_logger().info("Agent gateway is ready.")

    def _handle_text_command(self, msg: String) -> None:
        command = self._parse_delivery_command(msg.data)
        if command is None:
            self._speak("I did not understand the delivery command.")
            return
        self._dispatch_delivery(command)

    def _handle_gesture_command(self, msg: GestureCommand) -> None:
        if msg.command == "cancel":
            self._speak("Cancel gesture received. Cancel support will be connected to active goals next.")
        elif msg.command == "confirm_delivery":
            self.get_logger().info(f"Confirmation gesture from {msg.operator_id}.")

    def _parse_delivery_command(self, text: str) -> DeliveryCommand | None:
        match = self.COMMAND_RE.search(text.strip())
        if not match:
            return None
        operator_id = match.group("operator") or str(self.get_parameter("default_operator_id").value)
        return DeliveryCommand(
            tool_id=match.group("tool"),
            target_station=match.group("station"),
            operator_id=operator_id,
        )

    def _dispatch_delivery(self, command: DeliveryCommand) -> None:
        if not self._mission_client.wait_for_server(timeout_sec=0.1):
            self._speak("Mission controller is not ready.")
            return
        goal = DeliverTool.Goal()
        goal.tool_id = command.tool_id
        goal.target_station = command.target_station
        goal.operator_id = command.operator_id
        goal.require_confirmation = bool(self.get_parameter("require_confirmation").value)
        future = self._mission_client.send_goal_async(goal, feedback_callback=self._handle_mission_feedback)
        future.add_done_callback(self._handle_goal_response)
        self._speak(f"Starting delivery of {command.tool_id} to {command.target_station}.")

    def _handle_goal_response(self, future) -> None:
        goal_handle = future.result()
        if not goal_handle.accepted:
            self._speak("Delivery mission was rejected.")
            return
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._handle_mission_result)

    def _handle_mission_feedback(self, feedback_msg) -> None:
        feedback = feedback_msg.feedback
        self.get_logger().info(f"Mission feedback: {feedback.state} {feedback.progress:.0%} {feedback.detail}")

    def _handle_mission_result(self, future) -> None:
        result = future.result().result
        self._speak(result.message)

    def _speak(self, text: str) -> None:
        msg = String()
        msg.data = text
        self._tts_pub.publish(msg)
        self.get_logger().info(f"Agent says: {text}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = AgentGatewayNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

