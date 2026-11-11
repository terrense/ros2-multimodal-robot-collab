#!/usr/bin/env python3
"""Evaluate the named conditions against the L1 environment.

    python -m agent_harness.scripts.run_conditions --episodes 50 --out runs/conditions.json

The master plan asks for a specific set of baselines, each isolating one thing:
the rules, the expert, a one-shot plan, and four closed-loop configurations
that switch on receipts, the freshness gate and recovery in turn.

`rules` and `expert` are L1 policies and are scored by
`training.evaluate`. This script scores the *loop* conditions, because they
are a property of the runtime and not of a policy: the same policy run under
four configurations answers "what does the receipt buy?", "what does the
freshness gate buy?", "what does recovery buy?".

The report is a table, not a number: the point of the conditions is the
*differences*, and a single score would hide them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent_harness.agent.budgets import Budgets
from agent_harness.agent.conditions import LOOP_CONDITIONS, LoopCondition
from agent_harness.agent.loop import ClosedLoop
from agent_harness.agent.policy import Decide, EpisodeResult, Finish
from agent_harness.contracts import CapabilityRegistry, TaskOutcome
from agent_harness.runtime.client import ToolClient
from agent_harness.runtime.l1_backend import L1Backend
from agent_harness.runtime.snapshots import SnapshotBuilder

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The L1 episode limit, so the loop's turn budget bounds the run the same way
#: the simulator bounds its own.
MAX_EPISODE_STEPS = 30


class L1RulePolicy:
    """The rule baseline, expressed as a runtime policy.

    The rule table lives in `training.policies.scripted_baseline`; this adapts
    it to the runtime's decision type rather than re-implementing it, so the
    two scorers cannot drift apart.

    One subtlety: the rule baseline's last action is `mission.terminate`, and
    that action is what makes the environment record the outcome. So the
    terminate is returned as a *decision* -- it has to be executed -- and the
    policy only claims success on the turn after it, when the environment has
    confirmed. A policy that claimed success without letting the terminate run
    would be scored `false_success` every time, and rightly so: the claim
    would have been unverified.
    """

    name = "rules"

    def __init__(self, registry: CapabilityRegistry) -> None:
        from training.policies.scripted_baseline import RuleBaselinePolicy

        self._inner = RuleBaselinePolicy()
        self._registry = registry
        self._terminated = False
        self._claimed = False

    def reset(self) -> None:
        self._inner.reset()
        self._terminated = False
        self._claimed = False

    def decide(self, view: dict[str, Any]):
        if self._terminated:
            # The terminate action has run; the environment has the verdict
            # now. The claim is what that action asked for, not what we hope.
            return Finish(
                claimed_success=self._claimed,
                reason="the environment confirmed the outcome",
            )
        observation = _observation_from_view(view)
        if observation is None:
            return Finish(claimed_success=False, reason="no observation")
        action = self._inner.decide(observation)
        if action.skill == "mission" and action.action == "terminate":
            # Execute it first; the claim comes on the next turn, once the
            # environment has confirmed.
            self._terminated = True
            self._claimed = action.args.get("status") == "success"
            return Decide(
                capability="mission.terminate", arguments=dict(action.args)
            )
        return Decide(capability=f"{action.skill}.{action.action}", arguments=dict(action.args))

    def consume(self, receipt: dict[str, Any]) -> None:
        pass

    def tokens_used(self) -> int:
        return 0


def _observation_from_view(view: dict[str, Any]):
    """Rebuild an L1 observation from a runtime view.

    The view is the snapshot payload; the L1 observation is what the
    environment puts in it. When the condition hides the receipts, the
    observation's own `last_result` is gone too -- so a policy under
    `loop_no_receipts` genuinely cannot see what happened, and the rule
    baseline falls back to its default next action.
    """
    from training.envs.multi_turn_env import _observation_from_dict

    payload = view.get("payload") or {}
    data = {
        "step_index": int(payload.get("step_index", 0)),
        "task": payload.get("task") or {},
        "robot_location": payload.get("robot_location", ""),
        "holding_tool": payload.get("holding_tool"),
        "operator_verified": bool(payload.get("operator_verified", False)),
        "detections": payload.get("detections") or [],
        "last_action": payload.get("last_action"),
        "last_result": payload.get("last_result") or {},
        "safety_events": payload.get("safety_events") or [],
        "no_progress_count": int(payload.get("no_progress_count", 0)),
        "steps_remaining": int(payload.get("steps_remaining", 30)),
    }
    try:
        return _observation_from_dict(data)
    except Exception:  # noqa: BLE001 - a malformed view is a missing observation
        return None


def _run_one(
    env: Any,
    condition: LoopCondition,
    registry: CapabilityRegistry,
    episode_id: str,
) -> EpisodeResult:
    """One episode of one condition over the L1 environment."""
    backend = L1Backend(env)
    client = ToolClient(backend, registry, max_workers=1, platform="l1")
    builder = SnapshotBuilder(
        sensor_id="l1",
        calibration_version="calib-l1",
        frame_id="map",
    )
    state = {"version": 0}

    def observe():
        state["version"] += 1
        payload = env._observation().to_dict()
        return builder.build(payload, state_version=state["version"])

    policy = L1RulePolicy(registry)

    def outcome_source():
        """The environment's verdict, read from the L1 simulator.

        Without this the loop would have no way to tell a delivered episode
        from an abandoned one, and every episode would end as `false_success`:
        the policy claims success, nobody confirms it.
        """
        value = str(getattr(env.outcome, "value", env.outcome))
        if value in ("success", "correct_abort", "cancelled_ok"):
            return TaskOutcome(success=True, accepted=True, failure_category=None)
        return TaskOutcome(success=False, accepted=True, failure_category=value)

    loop = ClosedLoop(
        policy=policy,
        client=client,
        registry=registry,
        observe=observe,
        episode_id=episode_id,
        max_snapshot_age_s=1e9,
        condition=condition,
        platform="l1",
        max_observe_wait_s=0.1,
        observe_interval_s=0.01,
        outcome_source=outcome_source,
        budgets=Budgets(max_turns=MAX_EPISODE_STEPS + 2, max_wall_s=60.0),
    )
    try:
        return loop.run()
    finally:
        client.shutdown(5.0)


def _summarise(results: list[EpisodeResult]) -> dict[str, Any]:
    if not results:
        return {"episodes": 0}
    success = sum(1 for result in results if result.outcome.success is True)
    failed = sum(1 for result in results if result.failure)
    turns = sum(result.turns for result in results)
    recovered = sum(1 for result in results if result.outcome.success is not False)
    return {
        "episodes": len(results),
        "task_success": round(success / len(results), 4),
        "not_failed": round(recovered / len(results), 4),
        "failure_rate": round(failed / len(results), 4),
        "mean_turns": round(turns / len(results), 2),
        "failures": sorted({result.failure or "" for result in results if result.failure}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--split", default="train", help="scenario split; never 'benchmark'")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.split == "benchmark":
        raise SystemExit("refusing to evaluate on the held-out benchmark split")

    from agent_env.l1_sim import L1DeliveryEnv
    from training.data.scenario_bank import sample_scenarios

    registry = CapabilityRegistry.load(REPO_ROOT)
    specs = sample_scenarios(args.split, args.episodes)

    report: dict[str, Any] = {
        "version": "conditions_v1",
        "episodes": args.episodes,
        "split": args.split,
        "seed": args.seed,
        "conditions": {},
    }
    for condition in LOOP_CONDITIONS:
        results: list[EpisodeResult] = []
        for spec in specs:
            env = L1DeliveryEnv(spec.to_scenario(), seed=spec.seed)
            env.reset()
            results.append(_run_one(env, condition, registry, str(spec.scenario_id)))
        report["conditions"][condition.name] = {
            "condition": condition.describe(),
            "isolates": condition.isolates,
            **_summarise(results),
        }
        summary = report["conditions"][condition.name]
        print(
            f"{condition.name:24s} success={summary['task_success']:.1%} "
            f"turns={summary['mean_turns']:.1f} failures={summary['failure_rate']:.1%} "
            f"{summary.get('failures')}"
        )

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
