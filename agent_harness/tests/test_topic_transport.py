"""The topic transport must not spin the node when the node is already spun.

Publishing a message used to call `rclpy.spin_once(node)` unconditionally. In
serial mode that is right: the calling thread is the only spinner, so it has
to pump the node for the message to be processed. In concurrent mode it is
wrong: the background MultiThreadedExecutor is already spinning that node, and
a second spinner on one node makes callback ordering unpredictable.

It was not theoretical. The calibration probe publishes an emergency stop
while a motion is in flight, and the second spinner starved the executor: the
motion ran to completion on the server while the client waited out its full
timeout for a goal response that had already been sent.

The test needs no ROS: the executor's ROS imports are inside the constructor,
so the transport method can be driven with a stand-in node.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "agent_harness" / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))

import ros_tool_executor as executor_module  # noqa: E402  (path injected above)


class _FakeMessage:
    def __init__(self) -> None:
        self.command = ""


class _FakePublisher:
    def __init__(self) -> None:
        self.published: list[object] = []

    def publish(self, message: object) -> None:
        self.published.append(message)


class _FakeNode:
    def __init__(self) -> None:
        self.publisher = _FakePublisher()

    def create_publisher(self, message_type, endpoint, depth):
        return self.publisher


class _FakeRclpy:
    def __init__(self) -> None:
        self.spins: list[object] = []

    def spin_once(self, node, timeout_sec=None) -> None:
        self.spins.append(node)


def _fake_executor(concurrent: bool) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        node=_FakeNode(),
        rclpy=_FakeRclpy(),
        _executor=object() if concurrent else None,
    )


def _result() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        ros_type="std_msgs/msg/String",
        endpoint="/arm/control_command",
        payload={"command": "system_stop"},
        status="pending",
        result={},
        message="",
    )


class TopicTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = executor_module.import_ros_type
        executor_module.import_ros_type = lambda ros_type: _FakeMessage
        self.addCleanup(setattr, executor_module, "import_ros_type", self._original)

    def test_a_concurrent_publish_does_not_spin_the_node(self) -> None:
        fake = _fake_executor(concurrent=True)
        executor_module.RosToolExecutor._execute_topic(fake, _result())
        self.assertEqual(fake.rclpy.spins, [], "a second spinner on one node")
        self.assertTrue(fake.node.publisher.published)

    def test_a_serial_publish_spins_the_node(self) -> None:
        """Serial mode has no other spinner, so the calling thread pumps it."""
        fake = _fake_executor(concurrent=False)
        executor_module.RosToolExecutor._execute_topic(fake, _result())
        self.assertTrue(fake.rclpy.spins)


if __name__ == "__main__":
    unittest.main()
