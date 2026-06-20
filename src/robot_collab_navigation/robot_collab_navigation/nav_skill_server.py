import math
from dataclasses import dataclass
from pathlib import Path

import rclpy
import yaml
from action_msgs.msg import GoalStatus
from ament_index_python.packages import PackageNotFoundError, get_package_share_directory
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node
from rclpy.task import Future

from robot_collab_interfaces.action import NavigateToStation


def _async_sleep(node: Node, duration: float) -> Future:
    """Awaitable sleep driven by a ROS2 timer (see mission_state_machine for why)."""
    future = Future()

    def _on_timer() -> None:
        timer.cancel()
        timer.destroy()
        if not future.done():
            future.set_result(None)

    timer = node.create_timer(max(duration, 0.0001), _on_timer)
    return future


@dataclass(frozen=True)
class StationPose:
    station_id: str
    frame_id: str
    x: float
    y: float
    yaw: float
    role: str = ""


class NavSkillServer(Node):
    """Station-level navigation wrapper.

    In simulation mode this server emits deterministic progress. In Nav2 mode it
    resolves station ids from the station registry and dispatches NavigateToPose.
    """

    def __init__(self) -> None:
        super().__init__("nav_skill_server")
        self.declare_parameter("backend", "simulated")
        self.declare_parameter("simulate_step_seconds", 0.5)
        self.declare_parameter("known_stations", ["tool_shelf", "station_a", "station_b", "handover_zone"])
        self.declare_parameter("station_file", "")
        self.declare_parameter("nav2_action_name", "/navigate_to_pose")
        self.declare_parameter("nav2_server_timeout_sec", 5.0)
        self.declare_parameter("nav2_goal_timeout_sec", 120.0)
        self._stations = self._load_station_registry()
        self._nav2_client = ActionClient(
            self,
            NavigateToPose,
            str(self.get_parameter("nav2_action_name").value),
        )
        self._server = ActionServer(
            self,
            NavigateToStation,
            "/skills/navigate_to_station",
            execute_callback=self._execute_navigation,
            goal_callback=self._handle_goal,
            cancel_callback=self._handle_cancel,
        )
        backend = str(self.get_parameter("backend").value)
        station_count = len(self._known_station_ids())
        self.get_logger().info(f"Navigation skill server is ready: backend={backend}, stations={station_count}.")

    def _handle_goal(self, goal_request: NavigateToStation.Goal) -> GoalResponse:
        known = self._known_station_ids()
        if goal_request.station_id not in known:
            self.get_logger().warn(f"Unknown navigation station: {goal_request.station_id}")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _handle_cancel(self, _goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    async def _execute_navigation(self, goal_handle) -> NavigateToStation.Result:
        backend = str(self.get_parameter("backend").value).lower()
        if backend == "nav2":
            return await self._execute_nav2_navigation(goal_handle)
        if backend != "simulated":
            self.get_logger().warn(f"Unknown navigation backend '{backend}', falling back to simulated mode.")
        return await self._execute_simulated_navigation(goal_handle)

    async def _execute_simulated_navigation(self, goal_handle) -> NavigateToStation.Result:
        goal = goal_handle.request
        station = self._stations.get(goal.station_id)
        station_detail = ""
        if station is not None:
            station_detail = f" Target pose is ({station.x:.2f}, {station.y:.2f}, yaw={station.yaw:.2f})."
        steps = [
            ("ACCEPTED", 0.10, f"Accepted navigation request to {goal.station_id}."),
            ("LOCALIZING", 0.25, "Checking map frame and robot pose."),
            ("PLANNING", 0.40, "Planning global path."),
            ("MOVING", 0.75, f"Following path with local planner.{station_detail}"),
            ("ARRIVED", 1.00, f"Arrived at {goal.station_id}."),
        ]
        for state, progress, detail in steps:
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return self._result(False, "Navigation canceled.")
            self._publish_feedback(goal_handle, state, progress, detail)
            self.get_logger().info(f"[{state}] {detail}")
            await _async_sleep(self, float(self.get_parameter("simulate_step_seconds").value))

        goal_handle.succeed()
        return self._result(True, f"Reached {goal.station_id}.")

    async def _execute_nav2_navigation(self, goal_handle) -> NavigateToStation.Result:
        goal = goal_handle.request
        station = self._stations.get(goal.station_id)
        if station is None:
            goal_handle.abort()
            return self._result(False, f"Station {goal.station_id} has no registered pose.")

        server_timeout = float(self.get_parameter("nav2_server_timeout_sec").value)
        self._publish_feedback(goal_handle, "WAIT_FOR_NAV2", 0.05, "Waiting for Nav2 NavigateToPose.")
        if not self._nav2_client.wait_for_server(timeout_sec=server_timeout):
            goal_handle.abort()
            return self._result(False, "Nav2 NavigateToPose action server is unavailable.")

        nav_goal = NavigateToPose.Goal()
        nav_goal.pose = self._pose_from_station(station)
        self._publish_feedback(
            goal_handle,
            "DISPATCH_NAV2",
            0.12,
            f"Sending Nav2 goal to {goal.station_id} at ({station.x:.2f}, {station.y:.2f}).",
        )
        nav_goal_handle = await self._nav2_client.send_goal_async(
            nav_goal,
            feedback_callback=lambda msg: self._relay_nav2_feedback(goal_handle, msg),
        )
        if not nav_goal_handle.accepted:
            goal_handle.abort()
            return self._result(False, f"Nav2 rejected goal for {goal.station_id}.")

        result_future = nav_goal_handle.get_result_async()
        timeout = float(self.get_parameter("nav2_goal_timeout_sec").value)
        started_ns = self.get_clock().now().nanoseconds
        while not result_future.done():
            if goal_handle.is_cancel_requested:
                await nav_goal_handle.cancel_goal_async()
                goal_handle.canceled()
                return self._result(False, "Navigation canceled.")
            elapsed = (self.get_clock().now().nanoseconds - started_ns) / 1_000_000_000.0
            if timeout > 0.0 and elapsed > timeout:
                await nav_goal_handle.cancel_goal_async()
                goal_handle.abort()
                return self._result(False, f"Navigation to {goal.station_id} timed out.")
            await _async_sleep(self, 0.1)

        result = await result_future
        if result.status == GoalStatus.STATUS_SUCCEEDED:
            self._publish_feedback(goal_handle, "ARRIVED", 1.0, f"Nav2 reached {goal.station_id}.")
            goal_handle.succeed()
            return self._result(True, f"Reached {goal.station_id}.")

        goal_handle.abort()
        return self._result(False, f"Nav2 failed to reach {goal.station_id}; status={result.status}.")

    def _relay_nav2_feedback(self, goal_handle, nav_feedback) -> None:
        if goal_handle.is_cancel_requested:
            return
        feedback = nav_feedback.feedback
        distance = float(getattr(feedback, "distance_remaining", 0.0) or 0.0)
        progress = 0.50 if distance <= 0.0 else max(0.15, min(0.95, 1.0 / (1.0 + distance)))
        detail = f"Nav2 moving; distance remaining {distance:.2f} m."
        self._publish_feedback(goal_handle, "MOVING", progress, detail)

    def _load_station_registry(self) -> dict[str, StationPose]:
        station_file = str(self.get_parameter("station_file").value).strip()
        if station_file:
            path = Path(station_file)
        else:
            try:
                path = Path(get_package_share_directory("robot_collab_bringup")) / "config" / "stations.yaml"
            except PackageNotFoundError:
                self.get_logger().warn("robot_collab_bringup share directory not found; using known_stations only.")
                return {}

        if not path.exists():
            self.get_logger().warn(f"Station registry not found: {path}")
            return {}

        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}

        stations: dict[str, StationPose] = {}
        for station_id, raw in (data.get("stations") or {}).items():
            pose = raw.get("pose") or {}
            stations[station_id] = StationPose(
                station_id=station_id,
                frame_id=str(raw.get("frame_id", "map")),
                x=float(pose.get("x", 0.0)),
                y=float(pose.get("y", 0.0)),
                yaw=float(pose.get("yaw", 0.0)),
                role=str(raw.get("role", "")),
            )
        return stations

    def _known_station_ids(self) -> set[str]:
        configured = {str(station) for station in self.get_parameter("known_stations").value}
        return configured | set(self._stations)

    def _pose_from_station(self, station: StationPose) -> PoseStamped:
        pose = PoseStamped()
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.header.frame_id = station.frame_id
        pose.pose.position.x = station.x
        pose.pose.position.y = station.y
        pose.pose.position.z = 0.0
        pose.pose.orientation.z = math.sin(station.yaw * 0.5)
        pose.pose.orientation.w = math.cos(station.yaw * 0.5)
        return pose

    @staticmethod
    def _publish_feedback(goal_handle, state: str, progress: float, detail: str) -> None:
        feedback = NavigateToStation.Feedback()
        feedback.state = state
        feedback.progress = progress
        feedback.detail = detail
        goal_handle.publish_feedback(feedback)

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
