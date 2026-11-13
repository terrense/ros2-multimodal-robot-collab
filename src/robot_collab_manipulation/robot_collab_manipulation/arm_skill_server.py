import rclpy
from rcl_interfaces.msg import SetParametersResult
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node
from rclpy.task import Future
from std_msgs.msg import String

from robot_collab_interfaces.action import PickAndPlace
from robot_collab_interfaces.msg import ArmControlCommand


async def rclpy_sleep(node: Node, seconds: float) -> None:
    """Sleep usable inside rclpy-driven coroutines (no asyncio loop is running there)."""
    future = Future()
    timer = node.create_timer(seconds, lambda: future.done() or future.set_result(None))
    try:
        await future
    finally:
        node.destroy_timer(timer)


class ArmSkillServer(Node):
    """Arm and gripper wrapper for pick/place work.

    The arm is simulated, but it is not a machine that cannot fail. Two of
    the faults the calibration asks about are arm faults, and a stack whose
    arm cannot fail cannot answer the question at all:

    `miss_once`   the gripper closes on nothing, once. The next attempt
                  succeeds, which is what the recovery rate measures.
    `drop`        the tool is released in transit and falls. Where it fell
                  is published, so that a later attempt can pick it up from
                  there rather than from the shelf it came from.

    Which fault the arm has is a parameter, because that is how the
    calibration arranges the world: the scene driver sets `fault_mode` for
    the family under measurement and clears it afterwards. An arm that
    always succeeds measures nothing.
    """

    def __init__(self) -> None:
        super().__init__("arm_skill_server")
        self.declare_parameter("simulate_step_seconds", 0.5)
        self.declare_parameter("min_detection_confidence", 0.75)
        self.declare_parameter("fault_mode", "none")
        self.declare_parameter("drop_frame_prefix", "drop_zone")
        self._paused = False
        self._stopped = False
        self._last_control_command = "arm_start"
        #: Whether the next pick is the one that goes wrong.
        #:
        #: The fault is armed for a whole family of measurements, so it has
        #: to come back for every sample: the pick that fails, then the pick
        #: that recovers, then the next failure. A fault that fired once and
        #: never again would measure one sample and then report that the
        #: stack had nothing left to recover from.
        self._fault_pending = True
        #: The fault mode the arm was last asked for, so that arming a
        #: fault afresh starts it again.
        self._armed_mode = ""
        #: How many tools have been dropped, so that each one falls
        #: somewhere of its own.
        self._drops = 0
        self._drop_pub = self.create_publisher(String, "/manipulation/tool_dropped", 10)
        # Arming the fault is an event, not a state: the calibration sets
        # the parameter before every sample it measures, and the arm has to
        # notice that the fault was armed again even when the mode it is
        # set to is the one it is already in.
        self.add_on_set_parameters_callback(self._on_parameters_set)
        self._control_sub = self.create_subscription(
            ArmControlCommand,
            "/arm/control_command",
            self._handle_control_command,
            10,
        )
        self._server = ActionServer(
            self,
            PickAndPlace,
            "/skills/pick_and_place",
            execute_callback=self._execute_pick_and_place,
            goal_callback=self._handle_goal,
            cancel_callback=self._handle_cancel,
        )
        self.get_logger().info("Manipulation skill server is ready.")

    def _on_parameters_set(self, parameters: list) -> SetParametersResult:
        """Notice when the fault is armed.

        The callback fires on the setting, not on the reading, so the arm
        knows that the fault was armed again even when the mode is the one
        it is already in.

        The result matters: rclpy treats a callback that returns anything
        else as a refusal, and the parameter is then *not* set. An arm that
        says it is armed and is not is worse than an arm that says nothing.
        """
        for parameter in parameters:
            if parameter.name == "fault_mode":
                self._armed_mode = str(parameter.value).strip().lower()
                self._fault_pending = True
                self.get_logger().warn(f"Armed with fault mode '{self._armed_mode}'.")
        return SetParametersResult(successful=True)

    def _handle_control_command(self, msg: ArmControlCommand) -> None:
        self._last_control_command = msg.command
        if msg.command == "arm_pause":
            self._paused = True
            self.get_logger().warn(f"Arm paused by gesture from {msg.operator_id}: {msg.detail}")
        elif msg.command == "arm_start":
            self._paused = False
            self._stopped = False
            self.get_logger().info(f"Arm start/resume command from {msg.operator_id}.")
        elif msg.command == "system_stop":
            self._paused = False
            self._stopped = True
            self.get_logger().error(f"Emergency stop requested by {msg.operator_id}: {msg.detail}")

    def _handle_goal(self, goal_request: PickAndPlace.Goal) -> GoalResponse:
        if self._stopped:
            self.get_logger().warn("Rejected manipulation goal while emergency stop is active.")
            return GoalResponse.REJECT
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
        fault = str(self.get_parameter("fault_mode").value).strip().lower()
        self.get_logger().info(
            f"[PICK] fault mode '{fault}', pending={self._fault_pending}."
        )
        if fault != self._armed_mode:
            # The fault was armed (or disarmed) since the last pick: start it
            # again from the beginning.
            self._armed_mode = fault
            self._fault_pending = True
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
            if self._stopped:
                goal_handle.abort()
                return self._result(False, "Manipulation stopped by operator gesture.")
            while self._paused:
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                    return self._result(False, "Manipulation canceled while paused.")
                if self._stopped:
                    goal_handle.abort()
                    return self._result(False, "Manipulation stopped by operator gesture.")
                pause_feedback = PickAndPlace.Feedback()
                pause_feedback.state = "PAUSED"
                pause_feedback.progress = progress
                pause_feedback.detail = "Manipulator is paused by operator fist gesture."
                goal_handle.publish_feedback(pause_feedback)
                await rclpy_sleep(self, 0.2)
            feedback = PickAndPlace.Feedback()
            feedback.state = state
            feedback.progress = progress
            feedback.detail = detail
            goal_handle.publish_feedback(feedback)
            self.get_logger().info(f"[{state}] {detail}")
            await rclpy_sleep(self, float(self.get_parameter("simulate_step_seconds").value))
            if state == "GRASP" and fault == "miss_once" and self._fault_pending:
                # The gripper closed on nothing. The next attempt has to
                # work: a fault that repeated would measure a broken
                # gripper instead of a recovery.
                self._fault_pending = False
                goal_handle.abort()
                return self._result(
                    False,
                    f"The gripper closed on nothing; {goal.tool_id} was not grasped.",
                )
            if state == "LIFT" and fault == "drop" and self._fault_pending:
                # The tool was released in transit. Where it fell is
                # published, because a tool that fell somewhere unknown
                # cannot be picked up again.
                self._fault_pending = False
                frame = self._publish_drop()
                goal_handle.abort()
                return self._result(False, f"The tool was dropped at {frame}.")

        goal_handle.succeed()
        # The next pick is the one that goes wrong: the fault is a standing
        # condition of this arm, not a single accident.
        self._fault_pending = True
        return self._result(True, f"Moved {goal.tool_id} into {goal.target_frame}.")

    def _publish_drop(self) -> str:
        """Publish where the tool fell, and return that frame's name."""
        self._drops += 1
        frame = f"{str(self.get_parameter('drop_frame_prefix').value)}_{self._drops}"
        message = String()
        message.data = frame
        self._drop_pub.publish(message)
        self.get_logger().warn(f"The tool was dropped at {frame}.")
        return frame

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
