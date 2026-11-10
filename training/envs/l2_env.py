"""Running a live (L2) episode with the same surface as an L1 one.

The policy sees the same chat, the same observation shape and the same action
vocabulary in both levels; the only difference is where the action goes. That
is what makes the two levels comparable: an L1-trained policy can be pointed at
the live stack without a second prompt surface, and the evaluator scores both
with the same code.

Two things end an episode early, and both are recorded rather than smoothed
over:

- an action the live stack cannot perform at all (`UnsupportedAction`);
- a command whose physical effect is not established (`UncertainOutcome`).
  The effect may still be happening, so the episode stops instead of carrying
  on as if the command had never been dispatched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_env.l2_gazebo import L2GazeboEnv, UncertainOutcome, UnsupportedAction
from agent_env.protocol import ActionRegistry, ErrorCode, Outcome

from ..chat_format import build_messages, observation_content, parse_action, system_prompt
from .multi_turn_env import EpisodeRecord, TurnRecord

POLICY = Callable[[list[dict[str, str]]], str]


def run_l2_episode(
    policy: POLICY,
    env: L2GazeboEnv,
    *,
    task: dict[str, Any],
    episode_id: str,
    system: str | None = None,
    max_history_turns: int | None = None,
    provenance: Any = None,
    registry: ActionRegistry | None = None,
) -> EpisodeRecord:
    """Drive one live episode with a text policy."""
    catalogue = registry or ActionRegistry.load()
    prompt = system if system is not None else system_prompt(catalogue)

    observation = env.reset()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": observation_content(observation, include_brief=True)},
    ]
    steps: list[dict[str, Any]] = []
    turns: list[TurnRecord] = []
    full_messages: list[dict[str, str]] = list(messages)

    invalid = 0
    malformed = 0
    repaired = 0
    safety = 0
    outcome = Outcome.RUNNING.value
    failure_reason = ""
    done = False

    while not done:
        text = policy(messages)
        parsed = parse_action(text)
        action = parsed.action
        if parsed.status.value == "malformed":
            malformed += 1
        elif parsed.status.value == "repaired":
            repaired += 1

        before = observation.to_dict()
        try:
            observation, components, done, info = env.step(action)
        except UnsupportedAction as error:
            # The live stack cannot do this at all. That is a policy error, and
            # repeating it would only produce the same error.
            invalid += 1
            failure_reason = f"unsupported_action: {error}"
            turns.append(
                TurnRecord(
                    turn_index=len(turns) + 1,
                    observation=before,
                    assistant_text=text,
                    parse_status=parsed.status.value,
                    action=action.to_dict(),
                    result={
                        "status": "rejected",
                        "error_code": ErrorCode.INVALID_ACTION.value,
                        "message": str(error),
                    },
                    reward_components={},
                    info={"phase": None, "progress": 0.0},
                    invalid=True,
                    malformed=parsed.status.value == "malformed",
                    parse_error=str(error),
                )
            )
            outcome = Outcome.WRONG_TERMINATION.value
            break
        except UncertainOutcome as error:
            # The effect is not established. The episode stops; it does not
            # continue as if the command had not been dispatched.
            failure_reason = f"uncertain_outcome: {error}"
            outcome = Outcome.RUNNING.value
            break

        turns.append(
            TurnRecord(
                turn_index=len(turns) + 1,
                observation=before,
                assistant_text=text,
                parse_status=parsed.status.value,
                action=action.to_dict(),
                result=observation.last_result.to_dict(),
                reward_components=dict(components),
                info=dict(info),
                invalid=False,
                malformed=parsed.status.value == "malformed",
            )
        )
        steps.append(
            {"observation": before, "action": action.to_dict(), "assistant_text": text}
        )
        full_messages.append({"role": "assistant", "content": text})
        full_messages.append(
            {"role": "user", "content": observation_content(observation, include_brief=False)}
        )
        messages = build_messages(steps, registry=catalogue, system=prompt,
                                  max_history_turns=max_history_turns)

        if observation.last_result.status == "succeeded" and action.name == "mission.terminate":
            outcome = Outcome.SUCCESS.value
            done = True

    if outcome == Outcome.RUNNING.value and not failure_reason:
        outcome = Outcome.STEP_LIMIT.value if done else Outcome.WRONG_TERMINATION.value

    summary = {
        "outcome": outcome,
        "steps": len(turns),
        "invalid_actions": invalid,
        "malformed_actions": malformed,
        "repaired_actions": repaired,
        "safety_violations": safety,
        "grounding_violations": invalid,
        "recovered": outcome in (Outcome.SUCCESS.value, Outcome.CORRECT_ABORT.value),
        "delivered": outcome == Outcome.SUCCESS.value,
        "faults": [],
        "expected_abort": False,
        "failure_reason": failure_reason,
        "level": "l2",
    }
    return EpisodeRecord(
        episode_id=episode_id,
        scenario={"task": dict(task), "level": "l2"},
        seed=0,
        source="l2_ros2",
        turns=turns,
        summary=summary,
        messages=full_messages,
        provenance=provenance.to_dict() if provenance is not None else None,
    )


__all__ = ["run_l2_episode"]
