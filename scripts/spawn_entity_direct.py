#!/usr/bin/env python3
"""Spawn or delete a Gazebo entity through the service API.

``ros2 run gazebo_ros spawn_entity.py`` relies on ros2cli graph discovery,
which is unreliable in this WSL + CycloneDDS setup (services exist but the CLI
often cannot see them). Calling /spawn_entity directly with rclpy works.

Usage:
    spawn_entity_direct.py spawn NAME FILE X Y Z
    spawn_entity_direct.py delete NAME
"""

from __future__ import annotations

import sys
import time

import rclpy
from gazebo_msgs.srv import DeleteEntity, SpawnEntity
from geometry_msgs.msg import Pose


def _wait_for(node, name_fragment: str, timeout: float = 10.0) -> str | None:
    end = time.time() + timeout
    while time.time() < end:
        names = [n for n, _ in node.get_service_names_and_types() if name_fragment in n]
        if names:
            return names[0]
        rclpy.spin_once(node, timeout_sec=0.2)
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: spawn|delete ...", file=sys.stderr)
        return 2
    command = argv[0]
    rclpy.init()
    node = rclpy.create_node("spawn_entity_direct")
    try:
        if command == "delete":
            name = argv[1]
            service = _wait_for(node, "delete_entity")
            if service is None:
                print("delete_entity service unavailable")
                return 1
            client = node.create_client(DeleteEntity, service)
            client.wait_for_service(timeout_sec=5.0)
            request = DeleteEntity.Request()
            request.name = name
            future = client.call_async(request)
            rclpy.spin_until_future_complete(node, future, timeout_sec=20.0)
            result = future.result()
            print(f"delete {name}: {result.success if result else 'timeout'}")
            return 0 if result and result.success else 1

        name, path, x, y, z = argv[1], argv[2], float(argv[3]), float(argv[4]), float(argv[5])
        service = _wait_for(node, "spawn_entity")
        if service is None:
            print("spawn_entity service unavailable")
            return 1
        client = node.create_client(SpawnEntity, service)
        client.wait_for_service(timeout_sec=5.0)
        request = SpawnEntity.Request()
        request.name = name
        request.xml = open(path, encoding="utf-8").read()
        request.initial_pose = Pose()
        request.initial_pose.position.x = x
        request.initial_pose.position.y = y
        request.initial_pose.position.z = z
        future = client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=30.0)
        result = future.result()
        print(f"spawn {name}: {result.success if result else 'timeout'} "
              f"{result.status_message if result else ''}")
        return 0 if result and result.success else 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
