"""Every action server in the ROS 2 packages must be reachable through the runtime.

`/skills/pick_and_place` had a working action server and no schema entry, so
the capability registry never knew about it and the L2 adapter's grasp mapping
was rejected by the runtime -- every time, against a live stack. No test caught
it, because the schema tests read the schema and the package tests read the
packages, and nobody compared the two lists.

This is that comparison, done statically. It is the check that would have found
the defect without a robot.

The same reasoning applies to the timeout: a capability's `timeout_sec` was
computed into a policy and then discarded, so it was documentation. Both are
tested here because both were invisible until a live stack was driven.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_harness.contracts import ActionRequest, CapabilityDescriptor, CapabilityRegistry
from agent_harness.runtime.client import _effective_timeout
from agent_harness.runtime.policy import policy_for

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SKILLS = REPO_ROOT / "skills"

#: `ActionServer(self, Type, "/endpoint", ...)` in the package sources.
ACTION_SERVER = re.compile(r"ActionServer\(\s*[^,]+,\s*[^,]+,\s*[\"']([^\"']+)[\"']")


def _declared_endpoints() -> dict[str, str]:
    """endpoint -> the file that declares it."""
    found: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for match in ACTION_SERVER.finditer(path.read_text(encoding="utf-8")):
            found[match.group(1)] = str(path.relative_to(REPO_ROOT))
    return found


def _schema_endpoints() -> dict[str, str]:
    """endpoint -> "skill.action" as declared in the schemas."""
    found: dict[str, str] = {}
    for schema_path in sorted(SKILLS.glob("*/schema.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        skill = schema.get("name") or schema_path.parent.name
        for action, spec in (schema.get("actions") or {}).items():
            for key in ("ros_action", "ros_service", "ros_topic"):
                endpoint = spec.get(key)
                if endpoint:
                    found[endpoint] = f"{skill}.{action}"
    return found


def test_the_scan_finds_the_action_servers_it_should() -> None:
    """A regex that silently matches nothing would make every check below pass."""
    endpoints = _declared_endpoints()
    assert endpoints, "no ActionServer endpoints found; the scan is broken"
    for expected in (
        "/mission/deliver_tool",
        "/skills/pick_and_place",
        "/skills/navigate_to_station",
        "/skills/verify_operator",
    ):
        assert expected in endpoints, expected


def test_every_action_server_is_declared_in_a_schema() -> None:
    """The defect this file exists for: a server the registry never heard of."""
    unknown = {
        endpoint: source
        for endpoint, source in _declared_endpoints().items()
        if endpoint not in _schema_endpoints()
    }
    assert not unknown, (
        "these action servers have no schema entry, so the runtime cannot dispatch "
        f"them: {unknown}"
    )


def test_every_schema_action_endpoint_exists_in_the_sources() -> None:
    """The other direction: a declared endpoint nobody serves is a dead capability."""
    missing = {
        endpoint: capability
        for endpoint, capability in _schema_endpoints().items()
        if endpoint.startswith(("/skills/", "/mission/"))
        and endpoint not in _declared_endpoints()
    }
    assert not missing, f"declared but not served: {missing}"


def test_the_grasp_capability_specifically_is_dispatchable() -> None:
    """Pinned because it is the one that was missing, and it is easy to lose again."""
    registry = CapabilityRegistry.load(REPO_ROOT)
    descriptor = registry.require("manipulation.pick_and_place", "ros2")
    assert descriptor.resource_key == "arm"
    assert descriptor.model_visible is False


def test_no_capability_is_both_hidden_and_unreachable() -> None:
    """A capability hidden from the model must still be reachable by the runtime."""
    registry = CapabilityRegistry.load(REPO_ROOT)
    for descriptor in registry.for_platform("ros2"):
        if not descriptor.model_visible:
            assert registry.require(descriptor.name, "ros2") is descriptor


# --------------------------------------------------------------- the timeout


def _descriptor(timeout_s: float | None) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name="test.capability",
        available_in=frozenset({"ros2"}),
        timeout_s=timeout_s,
    )


def _request(timeout_s: float | None) -> ActionRequest:
    return ActionRequest(
        episode_id="ep-1",
        command_id="cmd-1",
        capability="test.capability",
        timeout_s=timeout_s,
    )


def test_a_capability_without_a_timeout_uses_the_client_default() -> None:
    policy = policy_for(_descriptor(None), default_timeout_s=60.0)
    assert _effective_timeout(_request(None), policy) == 60.0


def test_a_caller_may_ask_for_less_than_the_capability_allows() -> None:
    """This is how the nav_timeout probe produces its fault at all."""
    policy = policy_for(_descriptor(120.0), default_timeout_s=60.0)
    assert _effective_timeout(_request(1.0), policy) == 1.0


def test_a_caller_may_not_ask_for_more_than_the_capability_allows() -> None:
    policy = policy_for(_descriptor(30.0), default_timeout_s=60.0)
    assert _effective_timeout(_request(600.0), policy) == 30.0


def test_a_capability_timeout_is_used_when_the_caller_does_not_ask() -> None:
    """Before the fix this value was computed and then dropped on the floor."""
    policy = policy_for(_descriptor(30.0), default_timeout_s=60.0)
    assert _effective_timeout(_request(None), policy) == 30.0


def test_neither_side_setting_a_timeout_leaves_it_open() -> None:
    policy = policy_for(_descriptor(None), default_timeout_s=None)
    assert _effective_timeout(_request(None), policy) is None


@pytest.mark.parametrize("capability", ["navigation.navigate_to_station", "manipulation.pick_and_place"])
def test_the_shipped_capabilities_actually_carry_a_timeout(capability: str) -> None:
    """A schema without `timeout_sec` silently falls back to the client default."""
    registry = CapabilityRegistry.load(REPO_ROOT)
    descriptor = registry.require(capability, "ros2")
    assert descriptor.timeout_s is not None
    assert descriptor.timeout_s > 0
