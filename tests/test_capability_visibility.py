"""A capability can be dispatchable without being offered to the model.

`/skills/pick_and_place` is a real robot capability with its own action
server, and the runtime needs it: the L2 adapter maps L1's grasp vocabulary
onto it, and the calibration probes drive it. But a model that could call it
directly would bypass the mission state machine's sequence — verify the
operator, detect the tool, then pick — so it must not appear in the
model-facing capability list.

That is what `model_visible` separates. Before it existed, the only way to hide
a capability was `callable: false`, which also removed it from the runtime — so
the L2 adapter's grasp mapping could not work against a live stack.

These tests pin the separation, and the specific case that found it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_harness.contracts import CapabilityDescriptor, CapabilityRegistry
from agent_harness.contracts.capabilities import UnsupportedCapability

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_registry_carries_the_grasp_capability() -> None:
    registry = CapabilityRegistry.load(REPO_ROOT)
    assert "manipulation.pick_and_place" in registry
    descriptor = registry.get("manipulation.pick_and_place")
    assert descriptor.supports("ros2")
    assert descriptor.resource_key == "arm"


def test_the_grasp_capability_is_not_offered_to_the_model() -> None:
    registry = CapabilityRegistry.load(REPO_ROOT)
    visible = [descriptor.name for descriptor in registry.model_visible_for("ros2")]
    assert "manipulation.pick_and_place" not in visible
    assert "manipulation.pick_and_place" not in "\n".join(registry.describe_for("ros2"))


def test_the_runtime_may_still_dispatch_it() -> None:
    """This is the property the L2 adapter and the calibration probes need."""
    registry = CapabilityRegistry.load(REPO_ROOT)
    descriptor = registry.require("manipulation.pick_and_place", "ros2")
    assert descriptor.name == "manipulation.pick_and_place"


def test_the_model_facing_list_still_has_the_capabilities_it_should() -> None:
    registry = CapabilityRegistry.load(REPO_ROOT)
    visible = {descriptor.name for descriptor in registry.model_visible_for("ros2")}
    for expected in (
        "navigation.navigate_to_station",
        "perception.verify_operator",
        "mission-control.deliver_tool",
        "manipulation.publish_arm_control",
        "hri.publish_tts",
        "system-state.query_state",
    ):
        assert expected in visible, expected


def test_a_descriptor_defaults_to_visible() -> None:
    descriptor = CapabilityDescriptor(name="test.thing")
    assert descriptor.model_visible is True


def test_visibility_is_read_from_the_schema() -> None:
    descriptor = CapabilityDescriptor.from_schema_action(
        "skill",
        "action",
        {"required": [], "risk": "arm_motion", "model_visible": False},
        platform="ros2",
    )
    assert descriptor.model_visible is False


def test_merging_two_sources_keeps_a_capability_hidden_if_either_hides_it() -> None:
    registry = CapabilityRegistry()
    registry.add(
        CapabilityDescriptor(
            name="a.b", available_in=frozenset({"ros2"}), model_visible=True
        )
    )
    registry.add(
        CapabilityDescriptor(
            name="a.b", available_in=frozenset({"l1"}), model_visible=False
        )
    )
    merged = registry.get("a.b")
    assert not merged.model_visible
    assert merged.supports("ros2") and merged.supports("l1")


def test_an_unknown_capability_is_still_refused() -> None:
    registry = CapabilityRegistry.load(REPO_ROOT)
    with pytest.raises(UnsupportedCapability):
        registry.require("manipulation.no_such_thing", "ros2")
