#!/usr/bin/env python3
from __future__ import annotations

import argparse

import rclpy

from robot_collab_interfaces.srv import QuerySystemState


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requester", default="agent_supervisor")
    args = parser.parse_args()

    rclpy.init()
    node = rclpy.create_node("query_system_state")
    client = node.create_client(QuerySystemState, "/system/query_state")
    client.wait_for_service()
    request = QuerySystemState.Request()
    request.requester = args.requester
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future)
    print(future.result())
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
