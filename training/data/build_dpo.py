#!/usr/bin/env python3
"""Build DPO preference pairs whose labels are verified by the environment.

    python -m training.data.build_dpo --episodes 3000 --out data/dpo

At each expert decision the corruptors propose plausible-but-worse alternatives.
Every proposal is then *executed in a cloned environment* and both branches are
scored with the same composite step reward that PPO optimizes. A pair is emitted
only when the alternative is measurably worse by at least `--margin`.

That matters: hand-written "bad" actions are often not bad (an extra re-detect
can be free, a query costs one step and nothing else). Simulating instead of
asserting keeps those out of the dataset, and makes DPO and PPO agree on what
"better" means rather than optimizing two different notions of quality.

Granularity:

    step        one decision vs one decision -- sharp credit, no context drift.
    trajectory  a k-step window: the expert's continuation against the branch the
                bad decision leads to. This is where
                "grasp failed -> re-detect -> reposition -> retry" beats
                "grasp failed -> retry -> retry -> retry": both succeed, one wastes
                four steps.

Rule selection is round-robin over the rules that pass verification, so a rule
that applies everywhere (dropping a required argument) cannot swamp the corpus.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_env.expert_policy import ScriptedExpert
from agent_env.l1_sim import L1DeliveryEnv
from agent_env.protocol import Action, ActionRegistry, Observation

from ..chat_format import action_content, observation_content
from ..counterfactual import require_level, verify_clone
from ..envs import MultiTurnDeliveryEnv
from ..rewards import PRESETS, RewardConfig, compute_step_rewards
from .corruptors import CorruptionRule, propose_all
from .scenario_bank import ScenarioSpec, sample_scenarios


@dataclass
class Branch:
    """One simulated continuation: its messages and its score."""

    score: float
    messages: list[dict[str, str]]
    outcome: str
    turns: int


def clone_env(env: L1DeliveryEnv) -> L1DeliveryEnv:
    """Deep copy of world state, sharing the (immutable) action registry.

    The copy is verified before it is used. A counterfactual branch is only
    worth anything if it starts from exactly the state the original was in, so
    a clone whose state hash differs is refused rather than scored -- the
    preference pair would otherwise compare two different situations.
    """
    registry = env.registry
    env.registry = None  # type: ignore[assignment]
    try:
        clone = copy.deepcopy(env)
    finally:
        env.registry = registry
    clone.registry = registry
    require_level("l1")
    verify_clone(env, clone)
    return clone


def _step_dict(action: Action, components: dict[str, float], info: dict[str, Any], stuck: int) -> dict[str, Any]:
    return {
        "action": action.to_dict(),
        "reward_components": components,
        "info": {**info, "no_progress_count": stuck},
    }


def simulate(
    env: L1DeliveryEnv,
    first_action: Action,
    *,
    window: int,
    config: RewardConfig,
    expert: ScriptedExpert | None,
    persist: bool,
) -> Branch:
    """Run `first_action` then `window - 1` follow-ups in a clone, and score the window.

    `persist=True` repeats `first_action` (the blind-retry branch); otherwise the
    (copied) scripted expert continues, which measures the cost of recovering
    from the bad decision rather than the cost of never recovering.
    """
    clone = clone_env(env)
    follow = copy.deepcopy(expert) if expert is not None else None
    steps: list[dict[str, Any]] = []
    messages: list[dict[str, str]] = []
    action = first_action
    done = False
    turns = 0

    while turns < window and not done:
        messages.append({"role": "assistant", "content": action_content(action)})
        observation, components, done, info = clone.step(action)
        steps.append(_step_dict(action, components, info, observation.no_progress_count))
        turns += 1
        if done or turns >= window:
            break
        messages.append({"role": "user", "content": observation_content(observation, include_brief=False)})
        if persist:
            action = first_action
        elif follow is not None:
            action = follow.act(observation)
        else:
            break

    rewards = compute_step_rewards(steps, clone.outcome.value, config)
    return Branch(
        score=round(sum(reward.total for reward in rewards), 6),
        messages=messages,
        outcome=clone.outcome.value,
        turns=turns,
    )


def is_recovery_state(observation: Observation) -> bool:
    return bool(
        observation.last_result.status in ("failed", "rejected")
        or observation.last_result.error_code not in ("OK", "")
        or observation.no_progress_count >= 1
        or observation.safety_events
    )


def build_pairs(
    specs: list[ScenarioSpec],
    *,
    granularity: str,
    window: int,
    margin: float,
    max_pairs_per_episode: int,
    clean_turn_prob: float,
    config: RewardConfig,
    rng: random.Random,
    verbose: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    registry = ActionRegistry.load()
    rule_usage: Counter[str] = Counter()
    rejected_no_worse: Counter[str] = Counter()
    pairs: list[dict[str, Any]] = []
    effective_window = 1 if granularity == "step" else window

    for episode_index, spec in enumerate(specs, start=1):
        env = MultiTurnDeliveryEnv(
            spec.to_scenario(), seed=spec.seed, registry=registry, source="dpo_builder", episode_id=spec.scenario_id
        )
        env.reset()
        expert = ScriptedExpert()
        emitted = 0
        done = False

        while not done:
            observation = env.observation
            assert observation is not None
            expert_action = expert.act(observation)

            wanted = emitted < max_pairs_per_episode and (
                is_recovery_state(observation) or rng.random() < clean_turn_prob
            )
            if wanted:
                chosen = simulate(
                    env.env,
                    expert_action,
                    window=effective_window,
                    config=config,
                    expert=expert,
                    persist=False,
                )
                candidates: list[tuple[CorruptionRule, Branch]] = []
                for rule, bad_action in propose_all(observation, expert_action):
                    branch = simulate(
                        env.env,
                        bad_action,
                        window=effective_window,
                        config=config,
                        expert=expert,
                        persist=rule.persist,
                    )
                    if branch.score < chosen.score - margin:
                        candidates.append((rule, branch))
                    else:
                        rejected_no_worse[rule.name] += 1

                if candidates:
                    # Round-robin over rules, then largest verified gap.
                    rule, branch = min(candidates, key=lambda item: (rule_usage[item[0].name], item[1].score))
                    rule_usage[rule.name] += 1
                    emitted += 1
                    pairs.append(
                        {
                            "prompt": env.messages,
                            "chosen": chosen.messages,
                            "rejected": branch.messages,
                            "meta": {
                                "episode_id": spec.scenario_id,
                                "bucket": spec.bucket,
                                "turn_index": observation.step_index,
                                "granularity": granularity,
                                "rule": rule.name,
                                "failure_mode": rule.tag,
                                "recovery_state": is_recovery_state(observation),
                                "error_code": observation.last_result.error_code,
                                "chosen_score": chosen.score,
                                "rejected_score": branch.score,
                                "score_gap": round(chosen.score - branch.score, 6),
                                "rejected_outcome": branch.outcome,
                            },
                        }
                    )

            _, done, _ = env.step(action_content(expert_action))

        if verbose and episode_index % 500 == 0:
            print(f"  {episode_index}/{len(specs)} episodes -> {len(pairs)} pairs")

    stats = {
        "pairs": len(pairs),
        "episodes": len(specs),
        "pairs_per_rule": dict(sorted(rule_usage.items())),
        "proposals_rejected_as_not_worse": dict(sorted(rejected_no_worse.items())),
        "pairs_per_failure_mode": dict(sorted(Counter(pair["meta"]["failure_mode"] for pair in pairs).items())),
        "pairs_per_bucket": dict(sorted(Counter(pair["meta"]["bucket"] for pair in pairs).items())),
        "recovery_state_pairs": sum(1 for pair in pairs if pair["meta"]["recovery_state"]),
        "score_gap_mean": round(sum(pair["meta"]["score_gap"] for pair in pairs) / max(len(pairs), 1), 4),
        "score_gap_min": min((pair["meta"]["score_gap"] for pair in pairs), default=0.0),
    }
    return pairs, stats


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("data/dpo"))
    parser.add_argument("--episodes", type=int, default=3000)
    parser.add_argument("--split", default="train")
    parser.add_argument("--granularity", choices=("step", "trajectory"), default="step")
    parser.add_argument("--window", type=int, default=4, help="Trajectory granularity: turns per branch.")
    parser.add_argument("--margin", type=float, default=0.05, help="Minimum verified reward gap.")
    parser.add_argument("--max-pairs-per-episode", type=int, default=3)
    parser.add_argument("--clean-turn-prob", type=float, default=0.25, help="Chance of sampling a non-recovery state.")
    parser.add_argument("--reward-preset", default="progress", choices=sorted(PRESETS))
    parser.add_argument("--val-fraction", type=float, default=0.02)
    parser.add_argument("--seed", type=int, default=23)
    args = parser.parse_args()

    if args.split == "benchmark":
        raise SystemExit("refusing to build training data from the held-out benchmark split")

    specs = sample_scenarios(args.split, args.episodes)
    print(f"building {args.granularity} pairs from {len(specs)} episodes (reward={args.reward_preset})")
    pairs, stats = build_pairs(
        specs,
        granularity=args.granularity,
        window=args.window,
        margin=args.margin,
        max_pairs_per_episode=args.max_pairs_per_episode,
        clean_turn_prob=args.clean_turn_prob,
        config=PRESETS[args.reward_preset],
        rng=random.Random(args.seed),
    )

    episode_ids = sorted({pair["meta"]["episode_id"] for pair in pairs})
    random.Random(args.seed).shuffle(episode_ids)
    val_count = max(1, int(len(episode_ids) * args.val_fraction)) if args.val_fraction > 0 and episode_ids else 0
    val_ids = set(episode_ids[:val_count])

    train_pairs = [pair for pair in pairs if pair["meta"]["episode_id"] not in val_ids]
    val_pairs = [pair for pair in pairs if pair["meta"]["episode_id"] in val_ids]
    write_jsonl(args.out / "train.jsonl", train_pairs)
    write_jsonl(args.out / "val.jsonl", val_pairs)

    stats["train_pairs"] = len(train_pairs)
    stats["val_pairs"] = len(val_pairs)
    stats["config"] = {
        "granularity": args.granularity,
        "window": args.window,
        "margin": args.margin,
        "reward_preset": args.reward_preset,
        "split": args.split,
        "episodes": args.episodes,
    }
    (args.out / "stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
