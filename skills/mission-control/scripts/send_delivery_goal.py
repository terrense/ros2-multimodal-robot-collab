#!/usr/bin/env python3
from __future__ import annotations

import argparse

import rclpy
from rclpy.action import ActionClient

from robot_collab_interfaces.action import DeliverTool


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-id", required=True)
    parser.add_argument("--target-station", required=True)
    parser.add_argument("--operator-id", required=True)
    parser.add_argument("--no-confirmation", action="store_true")
    args = parser.parse_args()

    rclpy.init()
    node = rclpy.create_node("send_delivery_goal")
    client = ActionClient(node, DeliverTool, "/mission/deliver_tool")
    client.wait_for_server()
    goal = DeliverTool.Goal()
    goal.tool_id = args.tool_id
    goal.target_station = args.target_station
    goal.operator_id = args.operator_id
    goal.require_confirmation = not args.no_confirmation
    future = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, future)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
