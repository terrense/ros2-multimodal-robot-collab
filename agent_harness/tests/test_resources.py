"""Which resource a capability competes for.

The mapping is a safety decision, not a tidiness one, so it is pinned here.

**An emergency stop must not queue behind the motion it is meant to stop.** On
the same resource, the runtime delivers the stop when the arm has already
finished, and a stop that arrives after the motion has stopped nothing. That
is not theoretical: the calibration probe measured it on the live stack, 0/5
samples stopped, and the fix moved the control command to its own resource
and the same probe measured 5/5.

The control commands still serialise with each other, on that resource: they
are state changes to one thing and their order matters.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT))

from agent_harness.contracts.vocab import resource_key_for_risk  # noqa: E402


class ResourceKeyTests(unittest.TestCase):
    def test_a_motion_is_exclusive_with_itself(self) -> None:
        self.assertEqual(resource_key_for_risk("arm_motion"), "arm")
        self.assertEqual(resource_key_for_risk("base_motion"), "base")

    def test_a_stop_is_not_queued_behind_the_motion_it_stops(self) -> None:
        self.assertNotEqual(
            resource_key_for_risk("arm_control"),
            resource_key_for_risk("arm_motion"),
        )

    def test_control_commands_still_serialise_with_each_other(self) -> None:
        """They are state changes to one thing, so their order matters."""
        self.assertEqual(resource_key_for_risk("arm_control"), "arm_control")

    def test_everything_else_shares_the_episode(self) -> None:
        for risk in ("sensor_output", "read_only", "operator_feedback", None):
            self.assertEqual(resource_key_for_risk(risk), "episode")


class RegistryResourceTests(unittest.TestCase):
    """The descriptors have to agree with the vocabulary."""

    def test_the_registry_gives_the_control_command_its_own_resource(self) -> None:
        from agent_harness.contracts import CapabilityRegistry

        registry = CapabilityRegistry.load(REPO_ROOT)
        control = registry.require("manipulation.publish_arm_control", "ros2")
        motion = registry.require("manipulation.pick_and_place", "ros2")
        self.assertNotEqual(control.resource_key, motion.resource_key)


if __name__ == "__main__":
    unittest.main()
