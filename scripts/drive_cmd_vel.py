#!/usr/bin/env python3
"""Publish a fixed /cmd_vel for a fixed wall-clock duration, then stop."""

from __future__ import annotations

import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node


def main() -> None:
    linear_x = float(sys.argv[1]) if len(sys.argv) > 1 else 0.3
    angular_z = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    duration_sec = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0

    rclpy.init()
    node = Node("cmd_vel_driver")
    pub = node.create_publisher(Twist, "/cmd_vel", 10)

    print("waiting for a matched subscriber...")
    deadline = time.monotonic() + 10.0
    while pub.get_subscription_count() == 0 and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.2)
    print(f"subscriber_count={pub.get_subscription_count()}")

    move = Twist()
    move.linear.x = linear_x
    move.angular.z = angular_z

    started = time.monotonic()
    count = 0
    while time.monotonic() - started < duration_sec:
        pub.publish(move)
        count += 1
        rclpy.spin_once(node, timeout_sec=0.05)
        time.sleep(0.05)

    stop = Twist()
    pub.publish(stop)
    rclpy.spin_once(node, timeout_sec=0.1)
    print(f"published {count} velocity messages, then sent stop")

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
