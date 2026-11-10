"""The live stack behind the same interface as L1.

The ROS 2 side is lazy on purpose: the mapping, the refusals and the result
mapping can be tested here without a ROS 2 installation, and the honest
statements about what the live stack cannot do are the point of the module.
"""

from __future__ import annotations

import pytest

from agent_env.l2_gazebo import (
    ACTION_MAP,
    L2GazeboEnv,
    L2State,
    UncertainOutcome,
    UnsupportedAction,
    capability_for,
    receipt_to_result,
)
from agent_env.protocol import Action
from agent_harness.contracts import ExecutionReceipt, ExecutionStatus
from training.envs.l2_env import run_l2_episode


def _receipt(status: ExecutionStatus, detail: str = "", failure=None) -> ExecutionReceipt:
    return ExecutionReceipt(
        episode_id="ep-l2",
        command_id="cmd-1",
        execution_status=status,
        call_ok=status is ExecutionStatus.COMPLETED,
        # A rejected command never had a physical effect; a completed one is
        # the action server's claim, not a verified effect.
        physical_effect_verified=False if status is ExecutionStatus.REJECTED else None,
        failure_code=failure,
        detail=detail,
    )


class _FakeClient:
    """Just enough of the ToolClient for the adapter."""

    def __init__(self, receipt: ExecutionReceipt) -> None:
        self.receipt = receipt
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return self.receipt

    def wait(self, command_id, timeout=None):
        return self.receipt


# ------------------------------------------------------------------ the map


def test_every_l1_action_is_either_mapped_or_explained() -> None:
    import json
    from pathlib import Path

    from agent_env.protocol import AGENT_SKILLS_SCHEMA

    schema = json.loads(Path(AGENT_SKILLS_SCHEMA).read_text(encoding="utf-8"))
    names = set(schema["actions"])
    missing = names - set(ACTION_MAP)
    assert not missing, f"L1 actions with no L2 mapping at all: {sorted(missing)}"


def test_an_action_the_live_stack_cannot_do_is_refused_with_a_reason() -> None:
    with pytest.raises(UnsupportedAction) as error:
        capability_for(Action("navigation", "reposition", {}))
    assert "viewpoint" in str(error.value)


def test_the_composite_grasp_actions_say_so() -> None:
    capability = capability_for(Action("manipulation", "pick_tool", {"tool_id": "wrench"}))
    assert capability.capability == "manipulation.pick_and_place"
    assert capability.note == "composite"
    assert capability.build({"tool_id": "wrench"})["tool_id"] == "wrench"


def test_the_arguments_are_renamed_to_what_the_skill_expects() -> None:
    capability = capability_for(Action("navigation", "navigate_to_station", {"station_id": "tool_shelf"}))
    assert capability.build({"station_id": "tool_shelf", "reason": "go"}) == {
        "station_id": "tool_shelf",
        "reason": "go",
    }


# ------------------------------------------------------------- the receipts


def test_a_completed_action_is_a_succeeded_step() -> None:
    result = receipt_to_result(_receipt(ExecutionStatus.COMPLETED, "done"))
    assert result.status == "succeeded"
    assert result.error_code == "OK"


def test_a_rejected_action_keeps_its_failure_code() -> None:
    result = receipt_to_result(
        _receipt(ExecutionStatus.REJECTED, "nope", failure="unknown_capability")
    )
    assert result.status == "rejected"
    assert result.error_code == "unknown_capability"


def test_an_uncertain_effect_stops_the_episode() -> None:
    with pytest.raises(UncertainOutcome):
        receipt_to_result(_receipt(ExecutionStatus.UNKNOWN, "the goal may still be running"))


# ------------------------------------------------------------- the env


def _env(receipt: ExecutionReceipt, **kwargs) -> tuple[L2GazeboEnv, _FakeClient]:
    client = _FakeClient(receipt)
    env = L2GazeboEnv(client, task={"tool_id": "wrench", "target_station": "station_a"}, **kwargs)
    return env, client


def test_the_env_dispatches_through_the_client_and_folds_the_receipt_back() -> None:
    env, client = _env(_receipt(ExecutionStatus.COMPLETED, "arrived"))
    observation = env.reset()
    assert observation.task["tool_id"] == "wrench"

    observation, components, done, info = env.step(
        Action("navigation", "navigate_to_station", {"station_id": "tool_shelf", "reason": "go"})
    )
    assert client.requests[0].capability == "navigation.navigate_to_station"
    assert client.requests[0].arguments["station_id"] == "tool_shelf"
    assert observation.last_result.status == "succeeded"
    assert not done
    assert info["capability"] == "navigation.navigate_to_station"


def test_the_observation_does_not_invent_what_the_stack_did_not_report() -> None:
    """holding_tool and operator_verified are internal to the mission machine."""
    env, _client = _env(_receipt(ExecutionStatus.COMPLETED))
    env.reset()
    observation = env.step(Action("system-state", "query_state", {}))[0]
    assert observation.holding_tool is None
    assert observation.operator_verified is False
    assert observation.robot_location == ""


def test_the_state_can_be_folded_in_when_the_stack_reports_it() -> None:
    env, _client = _env(_receipt(ExecutionStatus.COMPLETED))
    env.reset()
    env.update_state(
        L2State(
            detections=[],
            robot_location="tool_shelf",
            holding_tool="wrench",
            operator_verified=True,
            mission_state="PICK_TOOL",
            mission_progress=0.64,
        )
    )
    observation = env.step(Action("system-state", "query_state", {}))[0]
    assert observation.robot_location == "tool_shelf"
    assert observation.holding_tool == "wrench"
    assert observation.operator_verified is True


# ------------------------------------------------------------ the episode


def test_a_live_episode_produces_the_same_record_shape_as_l1() -> None:
    env, client = _env(_receipt(ExecutionStatus.COMPLETED, "arrived"))

    def policy(messages):
        return (
            '{"skill": "navigation", "action": "navigate_to_station", '
            '"args": {"station_id": "tool_shelf", "reason": "go"}, "reason": "fetch"}'
        )

    record = run_l2_episode(
        policy,
        env,
        task={"tool_id": "wrench", "target_station": "station_a"},
        episode_id="ep-live-1",
    )
    assert record.source == "l2_ros2"
    assert record.summary["level"] == "l2"
    assert record.turns
    assert record.turns[0].result["status"] == "succeeded"
    assert client.requests


def test_an_unsupported_action_ends_the_episode_and_is_recorded() -> None:
    env, _client = _env(_receipt(ExecutionStatus.COMPLETED))

    def policy(messages):
        return (
            '{"skill": "navigation", "action": "reposition", '
            '"args": {"strategy": "viewpoint_left"}, "reason": "look again"}'
        )

    record = run_l2_episode(
        policy,
        env,
        task={"tool_id": "wrench", "target_station": "station_a"},
        episode_id="ep-live-2",
    )
    assert record.summary["outcome"] == "wrong_termination"
    assert "unsupported_action" in record.summary["failure_reason"]
    assert record.turns[0].invalid is True
