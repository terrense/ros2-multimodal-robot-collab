"""The rclpy half of the concurrency work (O3.1).

The pure-Python half — per-resource locks, and waiting without spinning — is in
`test_concurrency.py` and runs everywhere. This file is the other half: a real
`MultiThreadedExecutor`, real callback groups and a real action server, to check
that two *different* resources really do overlap and that two commands on the
*same* resource really do not.

It needs ROS 2, so it skips where `rclpy` is absent — the CI job for this
suite has no ROS installed. It is run in WSL, where the workspace is built:

    source /opt/ros/humble/setup.bash && source install/setup.bash
    python -m pytest agent_harness/tests/test_concurrency_ros.py -q

Two things are checked, and they are checked at different layers on purpose:

    the executor   two dispatches on different resources overlap in time
    the backend    two dispatches on the same resource do not

The second one is the lock's job, so it has to go through `RosBackend`; the
first one is the callback groups' job, so it goes straight to the executor.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")

from rclpy.action import ActionServer  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.node import Node  # noqa: E402

from agent_harness.contracts import ActionRequest  # noqa: E402
from agent_harness.runtime.ros_backend import RosBackend  # noqa: E402
from agent_harness.scripts.ros_tool_executor import RosToolExecutor, ToolCallResult  # noqa: E402

pytest.importorskip("robot_collab_interfaces")
from robot_collab_interfaces.action import NavigateToStation, PickAndPlace  # noqa: E402

GOAL_DURATION_S = 0.4


class _SlowServer:
    """An action server whose goal takes a fixed time, and which records when."""

    def __init__(self, action_type, endpoint: str, duration: float = GOAL_DURATION_S) -> None:
        self.duration = duration
        self.action_type = action_type
        self.intervals: list[tuple[float, float]] = []
        self.node = Node(f"test_server_{endpoint.strip('/').replace('/', '_')}")
        self.server = ActionServer(self.node, action_type, endpoint, self._execute)
        # Two threads, so the server *could* handle two goals at once. Whether it
        # does is up to the client: that is what makes this a test.
        self.executor = MultiThreadedExecutor(num_threads=2)
        self.executor.add_node(self.node)
        self._thread = threading.Thread(target=self.executor.spin, daemon=True)
        self._thread.start()

    def _execute(self, goal_handle):
        start = time.monotonic()
        time.sleep(self.duration)
        end = time.monotonic()
        self.intervals.append((start, end))
        goal_handle.succeed()
        return self.action_type.Result()

    def reset(self) -> None:
        """Forget the previous goals: the servers are shared by the tests."""
        self.intervals.clear()

    def stop(self) -> None:
        self.executor.shutdown(timeout_sec=2.0)
        self.node.destroy_node()


class _TestRegistry:
    """Routes capabilities to the test servers."""

    def __init__(self, routes: dict[str, tuple[str, str]]) -> None:
        self.routes = routes

    def get_action(self, skill: str, action: str) -> dict:
        endpoint, ros_type = self.routes[f"{skill}.{action}"]
        return {"ros_action": endpoint, "ros_type": ros_type, "timeout_sec": 20}


def _overlaps(intervals: list[tuple[float, float]]) -> bool:
    """Do two recorded intervals overlap in time?"""
    ordered = sorted(intervals)
    return len(ordered) > 1 and ordered[1][0] < ordered[0][1]


@pytest.fixture(scope="module")
def rig():
    """One executor, two servers, torn down in the right order.

    `sys.argv` is emptied for the duration: `RosToolExecutor` calls
    `rclpy.init(args=None)`, which would otherwise hand pytest's own command
    line to rclpy's argument parser.
    """
    original_argv = list(sys.argv)
    sys.argv = [original_argv[0]] if original_argv else ["pytest"]
    try:
        executor = RosToolExecutor(timeout_sec=20.0, concurrent=True)
        nav = _SlowServer(NavigateToStation, "/test/navigate")
        pick = _SlowServer(PickAndPlace, "/test/pick")
    finally:
        sys.argv = original_argv
    try:
        yield executor, nav, pick
    finally:
        executor.close()
        nav.stop()
        pick.stop()


def _request(resource_key: str, command_id: str, capability: str = "navigation.navigate_to_station") -> ActionRequest:
    arguments = {"station_id": "station_a", "reason": "concurrency test"}
    if capability.startswith("manipulation."):
        arguments = {"tool_id": "wrench", "source_frame": "tool_shelf", "target_frame": "handover_zone"}
    return ActionRequest(
        episode_id="ep-concurrency",
        command_id=command_id,
        capability=capability,
        arguments=arguments,
        resource_key=resource_key,
        timeout_s=20.0,
    )


NAV_PAYLOAD = {"station_id": "station_a", "reason": "concurrency test"}
PICK_PAYLOAD = {"tool_id": "wrench", "source_frame": "tool_shelf", "target_frame": "handover_zone"}


def _dispatch(executor, endpoint: str, ros_type: str, resource_key: str, command_id: str, payload: dict) -> None:
    record = ToolCallResult(
        step_index=1,
        skill="test",
        action="call",
        trace_id=command_id,
        mode="execute",
        status="pending",
        transport="action",
        endpoint=endpoint,
        ros_type=ros_type,
        message="",
        payload=dict(payload),
    )
    executor._execute_action(record, 20.0, resource_key)


def test_two_different_resources_overlap_in_time(rig) -> None:
    """The arm and the base are different hardware; they must not wait for each other."""
    executor, nav, pick = rig
    nav.reset()
    pick.reset()

    errors: list[BaseException] = []

    def call(server, endpoint, ros_type, resource_key, command_id, payload):
        try:
            _dispatch(executor, endpoint, ros_type, resource_key, command_id, payload)
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    first = threading.Thread(
        target=call,
        args=(
            nav,
            "/test/navigate",
            "robot_collab_interfaces/action/NavigateToStation",
            "base",
            "cmd-base",
            NAV_PAYLOAD,
        ),
    )
    second = threading.Thread(
        target=call,
        args=(
            pick,
            "/test/pick",
            "robot_collab_interfaces/action/PickAndPlace",
            "arm",
            "cmd-arm",
            PICK_PAYLOAD,
        ),
    )
    first.start()
    second.start()
    first.join(20.0)
    second.join(20.0)

    assert not errors, f"dispatch failed: {errors[0]}"
    assert nav.intervals, "the navigation goal never ran"
    assert pick.intervals, "the pick goal never ran"
    assert _overlaps(nav.intervals + pick.intervals), (
        "the two resources did not overlap: "
        f"nav={nav.intervals} pick={pick.intervals}"
    )


def test_the_same_resource_is_exclusive(rig) -> None:
    """Two commands on one arm compete for the same hardware: one must wait."""
    executor, nav, _pick = rig
    nav.reset()
    registry = _TestRegistry(
        {
            "navigation.navigate_to_station": (
                "/test/navigate",
                "robot_collab_interfaces/action/NavigateToStation",
            )
        }
    )
    backend = RosBackend(executor, registry)

    threads = [
        threading.Thread(target=backend.execute, args=(_request("arm", f"cmd-arm-{index}"),))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20.0)

    assert len(nav.intervals) == 2, f"both goals should have run: {nav.intervals}"
    assert not _overlaps(nav.intervals), (
        f"two commands on one resource overlapped: {nav.intervals}"
    )


def test_different_resources_are_not_exclusive(rig) -> None:
    """Through the backend, two resources still overlap: the lock is per resource."""
    executor, nav, pick = rig
    nav.reset()
    pick.reset()
    registry = _TestRegistry(
        {
            "navigation.navigate_to_station": (
                "/test/navigate",
                "robot_collab_interfaces/action/NavigateToStation",
            ),
            "manipulation.pick_and_place": (
                "/test/pick",
                "robot_collab_interfaces/action/PickAndPlace",
            ),
        }
    )
    backend = RosBackend(executor, registry)

    def call(resource_key: str, command_id: str, capability: str) -> None:
        backend.execute(_request(resource_key, command_id, capability))

    first = threading.Thread(
        target=call, args=("base", "cmd-a", "navigation.navigate_to_station")
    )
    second = threading.Thread(
        target=call, args=("arm", "cmd-b", "manipulation.pick_and_place")
    )
    first.start()
    second.start()
    first.join(20.0)
    second.join(20.0)

    assert nav.intervals, "the navigation goal never ran"
    assert pick.intervals, "the pick goal never ran"
    assert _overlaps(nav.intervals + pick.intervals), (
        f"the two resources did not overlap: nav={nav.intervals} pick={pick.intervals}"
    )
