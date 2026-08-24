"""Parsing must be forgiving about format and strict about content."""

from __future__ import annotations

import json

from agent_env.protocol import Action, ActionRegistry
from training.chat_format import (
    ParseStatus,
    action_content,
    build_messages,
    observation_content,
    parse_action,
    system_prompt,
)


def test_system_prompt_lists_every_registered_action() -> None:
    registry = ActionRegistry.load()
    prompt = system_prompt(registry)
    for name in registry.action_names:
        assert name in prompt
    assert "exactly one JSON action" in prompt


def test_action_content_roundtrip() -> None:
    action = Action("navigation", "navigate_to_station", {"station_id": "tool_shelf", "reason": "pick up"}, "go")
    parsed = parse_action(action_content(action))
    assert parsed.status is ParseStatus.OK
    assert parsed.action.name == "navigation.navigate_to_station"
    assert parsed.action.args["station_id"] == "tool_shelf"


def test_parse_strips_code_fence_and_prose() -> None:
    text = 'Sure, here is my step:\n```json\n{"skill":"perception","action":"verify_operator","args":{"operator_id":"operator_001"}}\n```'
    parsed = parse_action(text)
    assert parsed.status is ParseStatus.REPAIRED
    assert parsed.action.name == "perception.verify_operator"


def test_parse_keeps_only_post_think_answer() -> None:
    text = '<think>the gripper is empty so I should re-detect first</think>{"skill":"perception","action":"request_tool_detection","args":{"tool_id":"wrench"}}'
    parsed = parse_action(text)
    assert parsed.status is ParseStatus.REPAIRED
    assert parsed.action.args == {"tool_id": "wrench"}


def test_parse_accepts_dotted_name_and_arguments_alias() -> None:
    parsed = parse_action(json.dumps({"name": "manipulation.pick_tool", "arguments": {"tool_id": "hex_key", "grasp_id": "grasp_01"}}))
    assert parsed.action.skill == "manipulation"
    assert parsed.action.action == "pick_tool"
    assert parsed.action.args["grasp_id"] == "grasp_01"


def test_parse_accepts_flat_args() -> None:
    parsed = parse_action('{"skill":"navigation","action":"reposition","strategy":"step_back","reason":"blocked"}')
    assert parsed.action.args["strategy"] == "step_back"


def test_parse_repairs_trailing_comma() -> None:
    parsed = parse_action('{"skill":"hri","action":"publish_tts","args":{"text":"hello",},}')
    assert parsed.status is ParseStatus.REPAIRED
    assert parsed.action.args["text"] == "hello"


def test_malformed_output_is_reported_not_raised() -> None:
    for text in ["", "I will now pick up the wrench.", "{not json at all"]:
        parsed = parse_action(text)
        assert parsed.status is ParseStatus.MALFORMED
        assert parsed.action.name == "invalid.malformed_output"


def test_malformed_action_is_rejected_by_the_registry() -> None:
    registry = ActionRegistry.load()
    legal, _ = registry.validate(parse_action("nonsense").action)
    assert legal is False


def test_illegal_content_still_parses_cleanly() -> None:
    """A well-formed call to a non-existent skill is a policy error, not a parse error."""
    parsed = parse_action('{"skill":"navigation","action":"teleport","args":{"station_id":"mars"}}')
    assert parsed.status is ParseStatus.OK
    legal, message = ActionRegistry.load().validate(parsed.action)
    assert legal is False and "Unknown action" in message


def test_first_user_turn_carries_the_mission_brief() -> None:
    observation = {"step_index": 0, "task": {"tool_id": "wrench", "target_station": "station_a", "operator_id": "operator_001", "require_confirmation": True}}
    first = observation_content(observation)
    assert "Mission: deliver wrench to station_a" in first
    assert "OBSERVATION:" in first
    later = observation_content({**observation, "step_index": 3})
    assert "Mission:" not in later


def test_build_messages_alternates_roles_and_truncates() -> None:
    steps = [
        {"observation": {"step_index": index, "task": {"tool_id": "wrench"}}, "action": {"skill": "system-state", "action": "query_state", "args": {}, "reason": "check"}}
        for index in range(6)
    ]
    messages = build_messages(steps)
    assert messages[0]["role"] == "system"
    assert [message["role"] for message in messages[1:]] == ["user", "assistant"] * 6

    truncated = build_messages(steps, max_history_turns=2)
    assert sum(1 for message in truncated if message["role"] == "assistant") == 3  # first turn + last 2
    assert any("earlier turns elided" in message["content"] for message in truncated)
