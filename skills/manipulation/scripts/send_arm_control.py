#!/usr/bin/env python3
from __future__ import annotations

import argparse

import rclpy

from robot_collab_interfaces.msg import ArmControlCommand


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["arm_start", "arm_pause", "system_stop"])
    parser.add_argument("--operator-id", default="operator_001")
    parser.add_argument("--detail", default="")
    args = parser.parse_args()

    rclpy.init()
    node = rclpy.create_node("send_arm_control")
    pub = node.create_publisher(ArmControlCommand, "/arm/control_command", 10)
    msg = ArmControlCommand()
    msg.header.stamp = node.get_clock().now().to_msg()
    msg.command = args.command
    msg.operator_id = args.operator_id
    msg.source = "skill_script"
    msg.confidence = 1.0
    msg.emergency = args.command == "system_stop"
    msg.detail = args.detail
    pub.publish(msg)
    rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
