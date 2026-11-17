#!/usr/bin/env python3
"""Ask the navigation to drive the base to a station, and check that it did.

The check is the point. The navigation reports that it arrived, and that
report is worth nothing on its own: the base's own account of where it is
(the odometry) and the simulator's account (the model's pose) are two
numbers, and the distance between them is the wheel slip. A navigation
that reports an arrival nobody can vouch for is how a robot ends up
telling lies about where it is.

    scripts/drive_to_station.py station_a
    scripts/drive_to_station.py tool_shelf
"""

from __future__ import annotations

import math
import sys
import time

import rclpy
from gazebo_msgs.msg import ModelStates
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.node import Node

from robot_collab_interfaces.action import NavigateToStation

ROBOT = "collab_mobile_base"
ODOM_TOPIC = "/diff_drive_controller/odom"
STATES_TOPIC = "/model_states"
ACTION = "/skills/navigate_to_station"


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        station = argv[1]
    else:
        print(__doc__)
        return 2

    rclpy.init()
    node = Node("drive_to_station")
    state: dict[str, tuple[float, float] | None] = {"truth": None, "odom": None}

    node.create_subscription(
        ModelStates,
        STATES_TOPIC,
        lambda msg: state.__setitem__("truth", _model_pose(msg)),
        5,
    )
    node.create_subscription(
        Odometry,
        ODOM_TOPIC,
        lambda msg: state.__setitem__(
            "odom", (msg.pose.pose.position.x, msg.pose.pose.position.y)
        ),
        10,
    )

    client = ActionClient(node, NavigateToStation, ACTION)
    if not client.wait_for_server(timeout_sec=20.0):
        print("the navigation skill server is not there", flush=True)
        return 1

    goal = NavigateToStation.Goal()
    goal.station_id = station
    goal.reason = "drive_to_station.py"

    print(f"asking for {station}", flush=True)
    sent = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, sent, timeout_sec=10.0)
    handle = sent.result()
    if handle is None or not handle.accepted:
        print("the goal was rejected", flush=True)
        return 1

    finished = handle.get_result_async()
    deadline = time.monotonic() + 120.0
    while not finished.done() and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)

    outcome = finished.result().result
    truth = state["truth"]
    odom = state["odom"]
    print(f"result: {outcome.success} - {outcome.message}", flush=True)
    print(f"truth: {truth}", flush=True)
    print(f"odom:  {odom}", flush=True)
    if truth and odom:
        gap = math.hypot(truth[0] - odom[0], truth[1] - odom[1])
        print(f"the two disagree by {gap:.3f} m", flush=True)
    rclpy.shutdown()
    return 0 if outcome.success else 1


def _model_pose(msg: ModelStates) -> tuple[float, float] | None:
    """Where the chassis really is, according to the simulator."""
    for name, pose in zip(msg.name, msg.pose, strict=False):
        if name == ROBOT:
            return (float(pose.position.x), float(pose.position.y))
    return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
