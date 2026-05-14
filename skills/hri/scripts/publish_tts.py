#!/usr/bin/env python3
from __future__ import annotations

import argparse

import rclpy
from std_msgs.msg import String


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    args = parser.parse_args()

    rclpy.init()
    node = rclpy.create_node("publish_tts")
    pub = node.create_publisher(String, "/hri/tts_text", 10)
    msg = String()
    msg.data = args.text
    pub.publish(msg)
    rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
