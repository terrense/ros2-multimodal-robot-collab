import math
import time
from dataclasses import dataclass
from pathlib import Path

import rclpy
import yaml
from action_msgs.msg import GoalStatus
from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_share_directory,
)
from gazebo_msgs.msg import ModelStates
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node
from rclpy.task import Future

from robot_collab_interfaces.action import NavigateToStation


async def rclpy_sleep(node: Node, seconds: float) -> None:
    """Sleep usable inside rclpy-driven coroutines (no asyncio loop is running there)."""
    future = Future()
    timer = node.create_timer(seconds, lambda: future.done() or future.set_result(None))
    try:
        await future
    finally:
        node.destroy_timer(timer)


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
        self.declare_parameter("simulate_max_speed", 0.6)
        self.declare_parameter("simulate_arrive_tolerance", 0.15)
        self.declare_parameter("simulate_drive_timeout_sec", 60.0)
        self.declare_parameter("simulate_control_hz", 10.0)
        # How much the commanded velocity may change per control tick. The
        # controller imposes the wheel velocities every step, so a step in
        # the command is a step the chassis has to absorb.
        self.declare_parameter("simulate_accel_limit", 0.08)
        # The simulator's own account of the robot, so that a drive the
        # odometry reports and the chassis does not show is visible.
        self.declare_parameter("model_states_topic", "/model_states")
        self.declare_parameter("robot_model", "collab_mobile_base")
        self.declare_parameter("odometry_disagreement_m", 0.25)
        # How close is close enough when facing a station, and how long the
        # base may spend trying.
        self.declare_parameter("simulate_turn_tolerance_rad", 0.25)
        self.declare_parameter("simulate_turn_timeout_sec", 20.0)
        # Where the base takes its commands and where its pose comes from.
        #
        # The defaults are the classic names. A base driven by ros2_control
        # listens on the controller's own topics -- /diff_drive_controller/
        # cmd_vel_unstamped and /diff_drive_controller/odom -- so the launch
        # points these two parameters at them. Getting this wrong is quiet:
        # the navigation reports that it drove to the station and the robot
        # never moved.
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("odom_topic", "/odom")
        self._stations = self._load_station_registry()
        self._robot_model = str(self.get_parameter("robot_model").value)
        self._truth: tuple[float, float, float] | None = None
        #: What the navigation could not vouch for.
        self._warnings: list[str] = []
        #: A counter for the drive loop, so that the log says something once
        #: a second instead of ten times a second.
        self._ticks = 0
        self.create_subscription(
            ModelStates,
            str(self.get_parameter("model_states_topic").value),
            self._on_model_states,
            5,
        )
        self._cmd_vel = self.create_publisher(
            Twist, str(self.get_parameter("cmd_vel_topic").value), 10
        )
        self._cmd_vel_topic = str(self.get_parameter("cmd_vel_topic").value)
        self._odom: tuple[float, float, float] | None = None
        #: The velocity the base was last told to drive at. The command is
        #: ramped towards the wanted one: see `_publish_cmd_vel`.
        self._commanded_linear = 0.0
        self._commanded_angular = 0.0
        #: Where the base has been, and when, so the navigation can tell
        #: that it is not moving.
        self._progress: list[tuple[float, float, float, float]] = []
        #: Until when the base turns away from whatever it is stuck on.
        self._detour_until = 0.0
        self._detour_sign = 1.0
        self.create_subscription(
            Odometry,
            str(self.get_parameter("odom_topic").value),
            self._on_odom,
            10,
        )
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
        if backend.lower() == "nav2" and self._cmd_vel_topic != "/cmd_vel":
            # Nav2 publishes the classic /cmd_vel; the base listens on the
            # controller's own topic. This relay is what connects the two.
            # It is only wired up when the two differ, because a relay onto
            # the topic it reads from is a loop.
            self.create_subscription(Twist, "/cmd_vel", self._relay_cmd_vel, 10)
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
        """Drive the base to the station, for real.

        This backend used to publish progress and report success without
        moving: the robot stayed where it was, and every physical question was
        unaskable -- can the arm reach the tool, is the handover zone
        occupied, does the view of the tool change when the robot drives
        somewhere else. Those are exactly the questions the calibration asks.
        """
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
                self._stop()
                return self._result(False, "Navigation canceled.")
            self._publish_feedback(goal_handle, state, progress, detail)
            self.get_logger().info(f"[{state}] {detail}")
            if state == "MOVING" and station is not None:
                if not await self._drive_to(goal_handle, station):
                    self._stop()
                    return self._result(False, f"Could not reach {goal.station_id}.")
                if not await self._turn_to(goal_handle, station):
                    # The base is at the station; it just cannot face it, and
                    # a base that cannot rotate on the spot cannot be asked
                    # to. That is a warning, not a failed drive: the station
                    # was reached.
                    self._warnings.append(
                        f"the base reached {goal.station_id} but could not face it"
                    )
                    self.get_logger().warn(
                        f"The base reached {goal.station_id} and could not turn to "
                        "face it; its heading is off."
                    )
            else:
                await rclpy_sleep(self, float(self.get_parameter("simulate_step_seconds").value))

        disagreement = self._disagreement()
        if disagreement is not None and disagreement > float(
            self.get_parameter("odometry_disagreement_m").value
        ):
            # The odometry says the base arrived and the chassis says it
            # did not. Saying so is the point: a navigation that reports an
            # arrival nobody can vouch for is how a robot ends up telling
            # lies about where it is.
            self._warnings.append(
                f"the odometry and the chassis disagree by {disagreement:.2f} m at {goal.station_id}"
            )
            self.get_logger().warn(
                f"The odometry says the base reached {goal.station_id}, but the "
                f"chassis is {disagreement:.2f} m from where the odometry says it is."
            )
            self._publish_feedback(
                goal_handle,
                "ARRIVED",
                1.00,
                f"Reached {goal.station_id} according to the odometry; the "
                f"chassis is {disagreement:.2f} m away from there.",
            )

        goal_handle.succeed()
        return self._result(True, f"Reached {goal.station_id}.")

    async def _turn_to(self, goal_handle, station: StationPose) -> bool:
        """Turn to face the way the station is meant to be approached.

        A robot that arrives at a workstation and keeps the heading it drove
        in is a robot whose camera is pointing at the wall. The station's
        yaw is the way it is meant to face, and the camera is on the robot.

        The tolerance is loose on purpose. The base has no grip in this
        simulation, so the last degrees of a turn are the ones it cannot
        finish, and a robot that never finishes turning never arrives: the
        tolerance is what says "close enough to work at the station".
        """
        tolerance = float(self.get_parameter("simulate_turn_tolerance_rad").value)
        speed = 0.6
        timeout = float(self.get_parameter("simulate_turn_timeout_sec").value)
        period = 1.0 / float(self.get_parameter("simulate_control_hz").value)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if goal_handle.is_cancel_requested:
                self._stop()
                return False
            pose = self._steer_pose()
            if pose is None:
                await rclpy_sleep(self, period)
                continue
            _, _, yaw = pose
            error = (station.yaw - yaw + math.pi) % (2.0 * math.pi) - math.pi
            if abs(error) <= tolerance:
                self._stop()
                return True
            self._publish_cmd_vel(0.0, max(-1.0, min(1.0, speed * 2.0 * error)))
            await rclpy_sleep(self, period)
        self._stop()
        return False

    async def _drive_to(self, goal_handle, station: StationPose) -> bool:
        """Drive straight towards the station until the base is close enough.

        A straight line, because the stations are in the open part of the
        workcell. A real planner is what the `nav2` backend is for.
        """
        tolerance = float(self.get_parameter("simulate_arrive_tolerance").value)
        speed = float(self.get_parameter("simulate_max_speed").value)
        timeout = float(self.get_parameter("simulate_drive_timeout_sec").value)
        period = 1.0 / float(self.get_parameter("simulate_control_hz").value)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if goal_handle.is_cancel_requested:
                self._stop()
                return False
            pose = self._steer_pose()
            if pose is None:
                await rclpy_sleep(self, period)
                continue
            x, y, yaw = pose
            dx = station.x - x
            dy = station.y - y
            distance = math.hypot(dx, dy)
            self._last_drive = (x, y, yaw, distance)
            if distance <= tolerance:
                self._stop()
                return True
            heading = math.atan2(dy, dx)
            error = (heading - yaw + math.pi) % (2.0 * math.pi) - math.pi
            angular = max(-1.0, min(1.0, 2.0 * error))
            # Correct the heading *while* driving. A base that stops to turn
            # first has to rotate on the spot, and rotating on the spot
            # means sliding the wheels sideways: the base cannot do it, and
            # the drive times out with the base pointing the wrong way.
            #
            # But a base that drives forward while it turns hard drives
            # off in the direction it was already pointing: this one left
            # the tool shelf and climbed the panel that was standing in
            # front of it, and the drive that followed could not move it
            # because the base was on its side. So the further off the
            # heading is, the less the base drives: it turns first, on the
            # spot, and goes once it is facing the right way.
            alignment = math.cos(error)
            linear = speed * min(1.0, distance / 0.5) * abs(alignment) ** 2

            # A station behind the base is a different problem. The base
            # turns by driving in an arc, so a forward command with a hard
            # turn makes it drive in a circle: it comes back to where it
            # started, facing the other way, and the drive times out with
            # the station exactly as far away as it was. Backing up while
            # turning is what a wheeled robot with no sideways grip has to
            # do to turn around, and it is what this one does.
            if abs(error) > math.radians(100.0):
                linear = -0.25
                angular = max(-1.0, min(1.0, 2.0 * error))

            # An obstacle in the way. The base was asked to go somewhere and
            # it is not going: it is against a crate, or a wall, or the
            # panel the calibration puts up. A base that keeps pushing
            # against it never arrives, and a base that is allowed to drive
            # through it drives through whatever is in the way, which is
            # what a crate that only had a visual used to do.
            #
            # So the navigation watches whether the base is actually going
            # anywhere, and when it stops going anywhere it turns away for
            # a moment and tries again. It is a wall follower, not a
            # planner, and it says so: it gets around an obstacle by
            # sliding along it, and it does not know the difference between
            # a wall and a free path.
            stuck = self._stuck_against(x, y, yaw)
            if stuck:
                if self._detour_until <= time.monotonic():
                    # The sign changes when a detour *starts*, and not every
                    # time the base is found to be stuck: the base is found
                    # to be stuck several times a second, and a sign that
                    # changes that often is a base that wiggles in place
                    # instead of turning away.
                    self._detour_sign = 1.0 if self._detour_sign >= 0 else -1.0
                self._detour_until = time.monotonic() + 2.5
                self.get_logger().warn(
                    f"[DRIVE] not moving at ({x:.2f}, {y:.2f}); "
                    "turning away and trying again"
                )
            if self._detour_until > time.monotonic():
                # Backing away from whatever is in the way, while turning.
                # Turning on the spot does not work for this base: the
                # wheels grip sideways, so a base that is against something
                # cannot rotate out of it -- it has to reverse first.
                linear = -0.25
                angular = 0.6 * self._detour_sign
            self._publish_cmd_vel(linear, angular)
            self._ticks += 1
            if self._ticks % 10 == 0:
                # Once a second: enough to see what the base is doing, not
                # enough to bury the log.
                self.get_logger().info(
                    f"[DRIVE] at ({x:.2f}, {y:.2f}) yaw {yaw:.2f}, "
                    f"{distance:.2f} m to go, command v={linear:.2f} w={angular:.2f}"
                )
            await rclpy_sleep(self, period)
        self._stop()
        return False

    def _stuck_against(self, x: float, y: float, yaw: float) -> bool:
        """Whether the base is being told to move and is not moving.

        The base's own account of where it is, over the last second and a
        half: a base that was told to go somewhere and has not moved is
        against something.

        Two things count as not moving, and only both of them together mean
        stuck. The distance is the length of the path and not the distance
        from where the base was: a base that is turning on the spot travels
        plenty and gets nowhere, and calling that "not moving" stops the
        very turn that would have got it somewhere. And a base that has
        turned is turning, not stuck: the wheels are driving and the
        chassis is answering, and the turn is what gets it out of a place
        it cannot drive out of.

        The threshold for "has turned" is small, because this base turns
        badly: it scrubs its wheels rather than pivoting, and it turns at
        a fraction of the rate it is told to. A base that is jammed cannot
        turn at all, and that is the difference the threshold is about.
        """
        now = time.monotonic()
        self._progress.append((now, x, y, yaw))
        while self._progress and now - self._progress[0][0] > 1.5:
            self._progress.pop(0)
        if len(self._progress) < 5:
            return False
        travelled = 0.0
        for before, after in zip(self._progress, self._progress[1:], strict=False):
            travelled += math.hypot(after[1] - before[1], after[2] - before[2])
        turned = abs(
            (self._progress[-1][3] - self._progress[0][3] + math.pi)
            % (2.0 * math.pi)
            - math.pi
        )
        return travelled < 0.05 and turned < 0.05

    def _publish_cmd_vel(self, linear: float, angular: float) -> None:
        """Command the base, without slamming the wheels into the new speed.

        The controller imposes the wheel velocities every step, so a command
        that jumps from zero to full speed is a step the chassis has to
        absorb: the base pitches, and a base that is turning while it pitches
        goes over. The command is ramped instead, and the ramp is what a
        real base's acceleration limit does.
        """
        linear = float(linear)
        angular = float(angular)
        step = float(self.get_parameter("simulate_accel_limit").value)
        self._commanded_linear += max(
            -step, min(step, linear - self._commanded_linear)
        )
        self._commanded_angular += max(
            -step, min(step, angular - self._commanded_angular)
        )
        twist = Twist()
        twist.linear.x = self._commanded_linear
        twist.angular.z = self._commanded_angular
        self._cmd_vel.publish(twist)

    def _relay_cmd_vel(self, msg: Twist) -> None:
        """Forward a velocity command from the classic topic to the base."""
        self._cmd_vel.publish(msg)

    def _stop(self) -> None:
        """Stop the base now, not in a few ticks."""
        self._commanded_linear = 0.0
        self._commanded_angular = 0.0
        #: Where the base has been, and when, so the navigation can tell
        #: that it is not moving.
        self._progress: list[tuple[float, float, float, float]] = []
        #: Until when the base turns away from whatever it is stuck on.
        self._detour_until = 0.0
        self._detour_sign = 1.0
        twist = Twist()
        twist.linear.x = 0.0
        twist.angular.z = 0.0
        self._cmd_vel.publish(twist)

    def _on_odom(self, msg: Odometry) -> None:
        position = msg.pose.pose.position
        orientation = msg.pose.pose.orientation
        yaw = math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z),
        )
        self._odom = (float(position.x), float(position.y), float(yaw))

    def _on_model_states(self, msg: ModelStates) -> None:
        """Where the chassis really is.

        The simulator publishes the poses of everything in the world. The
        odometry is the robot's own account of where it is; this is the
        world's. When they disagree, the robot thinks it drove and it did
        not, and the navigation says so rather than reporting an arrival it
        cannot vouch for.

        The simulated backend steers by this pose. The wheels slip in this
        simulation, so the odometry counts more wheel rotation than the
        chassis travels, and steering by it would send the base in a curve
        that misses the station.
        """
        for name, pose in zip(msg.name, msg.pose, strict=False):
            if name != self._robot_model:
                continue
            orientation = pose.orientation
            yaw = math.atan2(
                2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
                1.0 - 2.0 * (orientation.y**2 + orientation.z**2),
            )
            self._truth = (float(pose.position.x), float(pose.position.y), yaw)

    def _pose(self) -> tuple[float, float, float] | None:
        """Where the base is, as far as the navigation can tell.

        The odometry. It is the robot's own account of where it is, and a
        navigation that used anything else would be a navigation that cannot
        work on a real robot. In this simulation the wheels slip, so the
        account is wrong -- and the disagreement with the simulator's own
        model states is reported at every arrival.
        """
        return self._odom

    def _steer_pose(self) -> tuple[float, float, float] | None:
        """Where the base is, for the simulated backend to steer by.

        The simulator's account of the chassis. The odometry counts wheel
        rotation, and the wheels slip in this simulation: the base was
        steering by the odometry, curving away from the station it was sent
        to, and arriving somewhere else entirely -- while the navigation
        reported an arrival. The disagreement between the two accounts is
        reported at every arrival (`_disagreement`), but a navigation that
        knows it is wrong and steers by it anyway is a navigation that
        cannot arrive.
        """
        return self._truth if self._truth is not None else self._odom

    def _disagreement(self) -> float | None:
        """How far the odometry is from where the chassis actually is."""
        if self._odom is None or self._truth is None:
            return None
        return math.hypot(
            self._odom[0] - self._truth[0], self._odom[1] - self._truth[1]
        )

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
            await rclpy_sleep(self, 0.1)

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
