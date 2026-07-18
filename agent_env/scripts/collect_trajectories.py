#!/usr/bin/env python3
"""Run the scripted expert on the L1 simulator and log trajectories.

Also serves as the Phase-0 acceptance check:

    python -m agent_env.scripts.collect_trajectories --acceptance --out data/l1_acceptance.jsonl

runs 50 fault-free episodes plus 10 per fault family and prints a per-bucket
report (outcome mix, recovery rate, schema legality, violations).
"""

from __future__ import annotations

import argparse
import random
import uuid
from collections import defaultdict
from pathlib import Path

from agent_env.faults import FaultInjector, FaultSpec, FaultType, default_params
from agent_env.l1_sim import L1DeliveryEnv, Scenario
from agent_env.expert_policy import ScriptedExpert
from agent_env.protocol import ActionRegistry, Outcome
from agent_env.trajectory_logger import TrajectoryLogger


def run_episode(
    scenario: Scenario,
    seed: int,
    logger: TrajectoryLogger,
    registry: ActionRegistry,
    source: str = "l1_expert",
) -> dict:
    env = L1DeliveryEnv(scenario, seed=seed, registry=registry)
    expert = ScriptedExpert()
    episode_id = f"ep-{uuid.uuid4().hex[:12]}"
    obs = env.reset()
    done = False
    illegal_actions = 0
    while not done:
        action = expert.act(obs)
        legal, _ = registry.validate(action)
        if not legal:
            illegal_actions += 1
        next_obs, reward_components, done, info = env.step(action)
        logger.log_step(episode_id, env.step_index, obs, action, env.last_result, reward_components, info)
        obs = next_obs
    summary = env.episode_summary()
    summary["illegal_actions"] = illegal_actions
    logger.log_episode(episode_id, scenario.to_dict(), summary, source, seed)
    return summary


def bucket_name(faults: list[FaultSpec]) -> str:
    if not faults:
        return "none"
    return "+".join(sorted(f.fault.value for f in faults))


def print_report(summaries: dict[str, list[dict]]) -> bool:
    print(f"\n{'bucket':<34}{'n':>4}{'recovered':>11}{'success':>9}{'abort':>7}{'cancel':>8}{'wrong':>7}{'limit':>7}{'avg_steps':>11}{'illegal':>9}{'safety':>8}")
    all_ok = True
    for bucket in sorted(summaries):
        rows = summaries[bucket]
        n = len(rows)
        recovered = sum(1 for r in rows if r["recovered"]) / n
        outcomes = defaultdict(int)
        for r in rows:
            outcomes[r["outcome"]] += 1
        avg_steps = sum(r["steps"] for r in rows) / n
        illegal = sum(r["illegal_actions"] for r in rows)
        safety = sum(r["safety_violations"] for r in rows)
        print(
            f"{bucket:<34}{n:>4}{recovered:>10.0%}{outcomes[Outcome.SUCCESS.value]:>9}"
            f"{outcomes[Outcome.CORRECT_ABORT.value]:>7}{outcomes[Outcome.CANCELLED_OK.value]:>8}"
            f"{outcomes[Outcome.WRONG_TERMINATION.value]:>7}{outcomes[Outcome.STEP_LIMIT.value]:>7}"
            f"{avg_steps:>11.1f}{illegal:>9}{safety:>8}"
        )
        if recovered < 1.0 or illegal > 0:
            all_ok = False
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--episodes", type=int, default=100, help="Episode count for --mix mode.")
    parser.add_argument("--mix", action="store_true", help="Sample none/single/double faults at 3:5:2.")
    parser.add_argument("--fault", type=str, default=None, help="Force a single fault type for every episode.")
    parser.add_argument("--acceptance", action="store_true", help="Phase-0 acceptance run: 50 clean + 10 per fault.")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    injector = FaultInjector(seed=args.seed)
    registry = ActionRegistry.load()
    summaries: dict[str, list[dict]] = defaultdict(list)

    jobs: list[list[FaultSpec]] = []
    if args.acceptance:
        jobs.extend([[]] * 50)
        for fault in FaultType:
            jobs.extend([[FaultSpec(fault, default_params(fault, rng))] for _ in range(10)])
    elif args.fault:
        fault = FaultType(args.fault)
        jobs.extend([[FaultSpec(fault, default_params(fault, rng))] for _ in range(args.episodes)])
    elif args.mix:
        jobs.extend(injector.sample() for _ in range(args.episodes))
    else:
        jobs.extend([[]] * args.episodes)

    with TrajectoryLogger(args.out) as logger:
        for index, faults in enumerate(jobs):
            scenario = Scenario.sample(rng, faults)
            summary = run_episode(scenario, seed=args.seed + index, logger=logger, registry=registry)
            summaries[bucket_name(faults)].append(summary)

    ok = print_report(summaries)
    total = sum(len(rows) for rows in summaries.values())
    print(f"\ntotal episodes: {total}  ->  {args.out}")
    if args.acceptance:
        print("acceptance:", "PASS" if ok else "FAIL (recovery < 100% or illegal actions present)")
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
