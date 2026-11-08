"""Tests for the agent harness plan validation and dispatch rendering.

These run without ROS2: ros_tool_executor only imports rclpy lazily inside the
execute path, so the validation/dry-run logic is pure Python. Run with either:

    python3 -m unittest discover -s agent_harness/tests
    pytest agent_harness/tests
"""

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "agent_harness" / "scripts"
SKILLS_DIR = REPO_ROOT / "skills"
EXAMPLES_DIR = REPO_ROOT / "agent_harness" / "examples"
SCHEMA_PATH = REPO_ROOT / "agent_harness" / "schemas" / "mission_plan.schema.json"

sys.path.insert(0, str(SCRIPTS_DIR))

from ros_tool_executor import (  # noqa: E402  (path injected above)
    PlanValidationError,
    SkillRegistry,
    dry_run_plan,
    load_plan,
    normalize_args,
    payload_for_call,
)


def load_example(name: str) -> dict:
    return load_plan(EXAMPLES_DIR / name)


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillRegistry.load(SKILLS_DIR)

    def test_known_skills_loaded(self) -> None:
        action = self.registry.get_action("mission-control", "deliver_tool")
        self.assertEqual(action["ros_action"], "/mission/deliver_tool")
        self.assertEqual(action["ros_type"], "robot_collab_interfaces/action/DeliverTool")

    def test_unknown_skill_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            self.registry.get_action("teleport", "anywhere")

    def test_unknown_action_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            self.registry.get_action("mission-control", "launch_rocket")


class DeliverPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillRegistry.load(SKILLS_DIR)
        self.plan = load_example("deliver_hex_key_plan.json")
        self.calls = self.registry.validate_plan(self.plan)

    def test_two_steps(self) -> None:
        self.assertEqual([(c.skill, c.action) for c in self.calls], [
            ("perception", "verify_operator"),
            ("mission-control", "deliver_tool"),
        ])

    def test_safety_defaults_normalized(self) -> None:
        verify_args = self.calls[0].args
        deliver_args = self.calls[1].args
        # require_face_match comes from safety.requires_face_auth (true)
        self.assertTrue(verify_args["require_face_match"])
        # require_confirmation comes from safety.requires_handover_confirmation (true)
        self.assertTrue(deliver_args["require_confirmation"])
        self.assertEqual(deliver_args["operator_id"], "operator_001")

    def test_dry_run_renders_action_dispatch(self) -> None:
        results = dry_run_plan(self.plan, self.calls, self.registry)
        deliver = results[1]
        self.assertEqual(deliver.transport, "action")
        self.assertEqual(deliver.endpoint, "/mission/deliver_tool")
        self.assertEqual(deliver.ros_type, "robot_collab_interfaces/action/DeliverTool")
        self.assertTrue(deliver.command.startswith("ros2 action send_goal /mission/deliver_tool"))
        self.assertEqual(deliver.result["risk"], "mission_motion")
        self.assertTrue(deliver.result["requires_confirmation"])


class EmergencyPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillRegistry.load(SKILLS_DIR)
        self.plan = load_example("emergency_stop_plan.json")
        self.calls = self.registry.validate_plan(self.plan)

    def test_single_arm_control_step(self) -> None:
        self.assertEqual(len(self.calls), 1)
        call = self.calls[0]
        self.assertEqual((call.skill, call.action), ("manipulation", "publish_arm_control"))

    def test_arm_control_defaults_and_payload(self) -> None:
        call = self.calls[0]
        self.assertEqual(call.args["command"], "system_stop")
        self.assertTrue(call.args["emergency"])
        self.assertEqual(call.args["operator_id"], "operator_001")
        self.assertEqual(call.args["source"], "agent_harness")
        payload = payload_for_call(call)
        self.assertEqual(payload["command"], "system_stop")
        self.assertTrue(payload["emergency"])
        self.assertIn("confidence", payload)

    def test_dry_run_renders_topic_dispatch(self) -> None:
        results = dry_run_plan(self.plan, self.calls, self.registry)
        item = results[0]
        self.assertEqual(item.transport, "topic")
        self.assertEqual(item.endpoint, "/arm/control_command")
        self.assertTrue(item.command.startswith("ros2 topic pub --once /arm/control_command"))


class NormalizeArgsTests(unittest.TestCase):
    """normalize_args injects safety/operator defaults from the plan; values
    explicitly provided in args must win, and plan-level safety toggles must
    flow through for the safety-bearing skills."""

    def _plan(self, safety=None) -> dict:
        plan = {"intent": "deliver_tool", "operator_id": "operator_042", "steps": []}
        if safety is not None:
            plan["safety"] = safety
        return plan

    def test_deliver_tool_safety_toggle_off(self) -> None:
        out = normalize_args(
            "mission-control", "deliver_tool", {},
            self._plan({"requires_handover_confirmation": False}),
        )
        self.assertFalse(out["require_confirmation"])
        self.assertEqual(out["operator_id"], "operator_042")

    def test_deliver_tool_safety_defaults_on(self) -> None:
        out = normalize_args("mission-control", "deliver_tool", {}, self._plan())
        self.assertTrue(out["require_confirmation"])

    def test_verify_operator_face_auth_toggle(self) -> None:
        out = normalize_args(
            "perception", "verify_operator", {},
            self._plan({"requires_face_auth": False}),
        )
        self.assertFalse(out["require_face_match"])

    def test_explicit_arg_overrides_default(self) -> None:
        out = normalize_args(
            "mission-control", "deliver_tool",
            {"operator_id": "operator_999", "require_confirmation": False},
            self._plan(),
        )
        self.assertEqual(out["operator_id"], "operator_999")
        self.assertFalse(out["require_confirmation"])

    def test_system_stop_marks_emergency(self) -> None:
        out = normalize_args(
            "manipulation", "publish_arm_control",
            {"command": "system_stop"}, self._plan(),
        )
        self.assertTrue(out["emergency"])
        self.assertEqual(out["source"], "agent_harness")
        self.assertEqual(out["confidence"], 1.0)


class ValidationErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillRegistry.load(SKILLS_DIR)

    def _plan(self, steps) -> dict:
        return {"intent": "deliver_tool", "operator_id": "operator_001", "steps": steps}

    def test_missing_top_level_fields(self) -> None:
        with self.assertRaises(PlanValidationError):
            self.registry.validate_plan({"intent": "deliver_tool"})

    def test_empty_steps_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            self.registry.validate_plan(self._plan([]))

    def test_missing_required_args(self) -> None:
        # deliver_tool requires tool_id + target_station; omit them.
        with self.assertRaises(PlanValidationError):
            self.registry.validate_plan(
                self._plan([{"skill": "mission-control", "action": "deliver_tool", "args": {}}])
            )

    def test_bad_command_enum(self) -> None:
        with self.assertRaises(PlanValidationError):
            self.registry.validate_plan(
                self._plan([
                    {"skill": "manipulation", "action": "publish_arm_control", "args": {"command": "explode"}}
                ])
            )


class SchemaConformanceTests(unittest.TestCase):
    """Lightweight stdlib-only conformance: every example must satisfy the
    mission_plan schema's required top-level keys and per-step required keys."""

    def setUp(self) -> None:
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def _required_top(self) -> set:
        return set(self.schema["required"])

    def _required_step(self) -> set:
        return set(self.schema["properties"]["steps"]["items"]["required"])

    def test_examples_conform(self) -> None:
        for path in sorted(EXAMPLES_DIR.glob("*.json")):
            plan = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(
                self._required_top() <= set(plan),
                f"{path.name} missing top-level keys",
            )
            for index, step in enumerate(plan["steps"], start=1):
                self.assertTrue(
                    self._required_step() <= set(step),
                    f"{path.name} step {index} missing required keys",
                )
            intent_enum = self.schema["properties"]["intent"]["enum"]
            self.assertIn(plan["intent"], intent_enum, f"{path.name} has unknown intent")


if __name__ == "__main__":
    unittest.main()
