import rclpy
from rclpy.node import Node
from std_msgs.msg import String

from robot_collab_interfaces.msg import GestureCommand, MissionEvent


class VoiceGatewayNode(Node):
    """ASR/TTS bridge and mission event narrator."""

    def __init__(self) -> None:
        super().__init__("voice_gateway_node")
        self.declare_parameter("tts_enabled", True)
        self.declare_parameter("forward_asr_to_agent", True)
        self._agent_text_pub = self.create_publisher(String, "/agent/command_text", 10)
        self._tts_pub = self.create_publisher(String, "/hri/tts_text", 10)
        self._asr_sub = self.create_subscription(String, "/hri/asr_text", self._handle_asr_text, 10)
        self._event_sub = self.create_subscription(MissionEvent, "/mission/events", self._handle_mission_event, 10)
        self._gesture_sub = self.create_subscription(
            GestureCommand,
            "/hri/gesture_command",
            self._handle_gesture,
            10,
        )
        self.get_logger().info("Voice gateway is ready.")

    def _handle_asr_text(self, msg: String) -> None:
        text = msg.data.strip()
        if not text:
            return
        self.get_logger().info(f"ASR text received: {text}")
        if bool(self.get_parameter("forward_asr_to_agent").value):
            outbound = String()
            outbound.data = text
            self._agent_text_pub.publish(outbound)

    def _handle_mission_event(self, msg: MissionEvent) -> None:
        if not bool(self.get_parameter("tts_enabled").value):
            return
        spoken = self._event_to_spoken_text(msg)
        if spoken:
            self._publish_tts(spoken)

    def _handle_gesture(self, msg: GestureCommand) -> None:
        if msg.command == "confirm_delivery":
            self._publish_tts("Confirmation received. Continuing the delivery.")
        elif msg.command == "cancel":
            self._publish_tts("Cancel request received.")

    def _event_to_spoken_text(self, event: MissionEvent) -> str:
        state_map = {
            "VERIFY_IDENTITY": "Verifying operator identity.",
            "DETECT_TOOL": "Searching for the requested tool.",
            "NAVIGATE_TO_TOOL": "Moving to the tool station.",
            "PICK_TOOL": "Picking up the tool.",
            "NAVIGATE_TO_OPERATOR": "Delivering the tool.",
            "PLACE_AND_HANDOVER": "Placing the tool in the handover area.",
            "COMPLETED": "Delivery complete.",
            "CANCELED": "Delivery canceled.",
        }
        return state_map.get(event.state, "")

    def _publish_tts(self, text: str) -> None:
        msg = String()
        msg.data = text
        self._tts_pub.publish(msg)
        self.get_logger().info(f"TTS: {text}")


def main(args=None) -> None:
    rclpy.init(args=args)
    node = VoiceGatewayNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

