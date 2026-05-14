#!/usr/bin/env python3
from __future__ import annotations

import argparse

import rclpy
from rclpy.action import ActionClient

from robot_collab_interfaces.action import NavigateToStation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--station-id", required=True)
    parser.add_argument("--reason", default="agent requested navigation")
    args = parser.parse_args()

    rclpy.init()
    node = rclpy.create_node("send_navigation_goal")
    client = ActionClient(node, NavigateToStation, "/skills/navigate_to_station")
    client.wait_for_server()
    goal = NavigateToStation.Goal()
    goal.station_id = args.station_id
    goal.reason = args.reason
    future = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, future)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
