#!/usr/bin/env python3
"""One-shot read of the current /odom position+yaw."""

from __future__ import annotations

import math

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node


def main() -> None:
    rclpy.init()
    node = Node("odom_reader")
    captured: list[Odometry] = []
    node.create_subscription(Odometry, "/odom", lambda msg: captured.append(msg), 10)

    deadline = node.get_clock().now().nanoseconds + 10_000_000_000
    while not captured and node.get_clock().now().nanoseconds < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)

    if not captured:
        print("TIMEOUT: no /odom message received")
    else:
        p = captured[-1].pose.pose.position
        q = captured[-1].pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        print(f"x={p.x:.4f} y={p.y:.4f} yaw_deg={math.degrees(yaw):.2f}")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
