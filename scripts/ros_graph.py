#!/usr/bin/env python3
"""Query the ROS graph without the `ros2` CLI.

Under WSL with CycloneDDS the `ros2` CLI frequently cannot see a graph whose
nodes are all running and talking to each other — the CLI's own discovery is
what fails. The earlier session hit this and wrote
`scripts/spawn_entity_direct.py` to work around it for one service. This is
the same approach generalised: ask the graph from inside a node.

    python3 scripts/ros_graph.py nodes
    python3 scripts/ros_graph.py topics
    python3 scripts/ros_graph.py services
    python3 scripts/ros_graph.py actions
    python3 scripts/ros_graph.py call /system/query_state \
        robot_collab_interfaces/srv/QuerySystemState "{requester: 'probe'}"
    python3 scripts/ros_graph.py wait /mission/deliver_tool 30

The `call` and `wait` subcommands exist because they are the two things a
smoke test needs and the two the CLI cannot do here.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from typing import Any


def _import_type(ros_type: str) -> Any:
    """`pkg/msg/Type` -> the class."""
    parts = ros_type.split("/")
    if len(parts) != 3:
        raise SystemExit(f"unsupported ROS type string: {ros_type}")
    package, namespace, name = parts
    module = importlib.import_module(f"{package}.{namespace}")
    return getattr(module, name)


def _spin_until(node: Any, future: Any, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not future.done():
        if time.monotonic() > deadline:
            return False
        node.get_clock()
        import rclpy

        rclpy.spin_once(node, timeout_sec=0.1)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("nodes")
    sub.add_parser("topics")
    sub.add_parser("services")
    sub.add_parser("actions")
    call = sub.add_parser("call")
    call.add_argument("service")
    call.add_argument("type")
    call.add_argument("request", nargs="?", default="{}")
    wait = sub.add_parser("wait")
    wait.add_argument("name")
    wait.add_argument("timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    import rclpy

    rclpy.init()
    node = rclpy.create_node("ros_graph_probe")
    try:
        if args.command == "nodes":
            for name, _namespace in sorted(node.get_node_names_and_namespaces()):
                print(name)
            return 0
        if args.command == "topics":
            for name, types in sorted(node.get_topic_names_and_types()):
                print(f"{name}  {','.join(types)}")
            return 0
        if args.command == "services":
            for name, types in sorted(node.get_service_names_and_types()):
                print(f"{name}  {','.join(types)}")
            return 0
        if args.command == "actions":
            # rclpy has no graph-level action listing; the action servers show
            # up as topics, which is enough to see them.
            for name, _types in sorted(node.get_topic_names_and_types()):
                if "_action/" in name:
                    print(name)
            return 0
        if args.command == "call":
            service_type = _import_type(args.type)
            client = node.create_client(service_type, args.service)
            if not client.wait_for_service(timeout_sec=10.0):
                print(f"service unavailable: {args.service}", file=sys.stderr)
                return 1
            request = service_type.Request()
            payload = json.loads(args.request)
            for key, value in payload.items():
                if not hasattr(request, key):
                    print(f"no field {key!r} in {args.type}", file=sys.stderr)
                    return 2
                setattr(request, key, value)
            future = client.call_async(request)
            if not _spin_until(node, future, 30.0):
                print("timed out", file=sys.stderr)
                return 3
            result = future.result()
            print(result)
            return 0
        if args.command == "wait":
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                services = [name for name, _ in node.get_service_names_and_types()]
                topics = [name for name, _ in node.get_topic_names_and_types()]
                # An action server shows up as a topic pair, not a service.
                if any(args.name in name for name in services) or any(
                    args.name in name for name in topics
                ):
                    print(f"{args.name} is up")
                    return 0
                import rclpy

                rclpy.spin_once(node, timeout_sec=0.2)
            print(f"{args.name} did not appear within {args.timeout}s", file=sys.stderr)
            return 1
    finally:
        node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
