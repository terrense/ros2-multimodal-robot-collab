"""The runtime has to be able to drive L1 as well as ROS 2.

Phase 6 wants L1 and L2 under the same high-level semantics, so the same
client, the same loop and the same receipts have to work over both. They did
not: `ToolClient` hard-coded the "ros2" platform, so every capability that
exists only in the L1 vocabulary -- `navigation.reposition`,
`perception.request_tool_detection` -- was rejected with `unknown_capability`.

And the failure was silent in the worst way. The client returned a *rejected
receipt*, which is a perfectly valid receipt, so the loop carried on: the
caller saw thirty-two turns of polite rejections instead of an error, and the
environment never advanced a single step.

These tests pin the platform as a property of the client.
"""

from __future__ import annotations

from pathlib import Path

from agent_harness.contracts import (
    ActionRequest,
    CapabilityRegistry,
    ExecutionStatus,
    new_command_id,
)
from agent_harness.runtime.client import ToolClient
from agent_harness.runtime.fake_backend import FakeBackend

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)


def _request(capability: str, **arguments) -> ActionRequest:
    return ActionRequest(
        episode_id="ep-platform",
        command_id=new_command_id(),
        capability=capability,
        arguments=arguments,
    )


def _client(platform: str) -> tuple[ToolClient, FakeBackend]:
    backend = FakeBackend()
    return ToolClient(backend, REGISTRY, platform=platform), backend


def _submit_and_wait(client: ToolClient, capability: str, **arguments):
    """Submit and wait for the receipt.

    `submit` returns a *queued* receipt; the backend runs on the scheduler's
    worker. Checking the backend without waiting would race it.
    """
    request = _request(capability, **arguments)
    submitted = client.submit(request)
    if submitted.execution_status is ExecutionStatus.QUEUED:
        return client.wait(request.command_id)
    return submitted


def test_a_capability_that_exists_only_on_l1_is_rejected_on_ros2() -> None:
    """The pre-fix behaviour, kept as the contrast."""
    client, _backend = _client("ros2")
    receipt = _submit_and_wait(
        client, "navigation.reposition", strategy="viewpoint_left", reason="look again"
    )
    assert receipt.execution_status is ExecutionStatus.REJECTED
    assert receipt.failure_code is not None


def test_the_same_capability_is_dispatched_on_l1() -> None:
    client, backend = _client("l1")
    receipt = _submit_and_wait(
        client, "navigation.reposition", strategy="viewpoint_left", reason="look again"
    )
    assert receipt.execution_status is not ExecutionStatus.REJECTED
    assert backend.calls, "the backend was never called"


def test_a_capability_that_exists_only_on_ros2_is_rejected_on_l1() -> None:
    """The platform cuts both ways."""
    client, _backend = _client("l1")
    receipt = _submit_and_wait(
        client, "mission-control.deliver_tool", tool_id="wrench", target_station="station_a"
    )
    assert receipt.execution_status is ExecutionStatus.REJECTED


def test_the_platform_defaults_to_ros2() -> None:
    """The default must not change what existing callers do."""
    client, _backend = _client("ros2")
    receipt = _submit_and_wait(
        client, "navigation.navigate_to_station", station_id="station_a", reason="go"
    )
    assert receipt.execution_status is not ExecutionStatus.REJECTED


def test_a_capability_available_on_both_works_on_either() -> None:
    for platform in ("l1", "ros2"):
        client, backend = _client(platform)
        receipt = _submit_and_wait(
            client, "perception.verify_operator", operator_id="operator_001"
        )
        assert receipt.execution_status is not ExecutionStatus.REJECTED, platform
        assert backend.calls, platform


def test_the_rejection_is_visible_rather_than_silent() -> None:
    """A rejected capability must say why, or the caller cannot act on it."""
    client, _backend = _client("l1")
    receipt = _submit_and_wait(
        client, "mission-control.deliver_tool", tool_id="wrench", target_station="station_a"
    )
    assert receipt.execution_status is ExecutionStatus.REJECTED
    assert receipt.detail, "a rejection with no reason is as good as no rejection"
    assert "l1" in receipt.detail
