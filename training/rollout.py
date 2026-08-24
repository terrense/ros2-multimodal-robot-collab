#!/usr/bin/env python3
"""Batched rollout driver: run a policy over a scenario set and log trajectories.

    python -m training.rollout --policy rules --benchmark benchmarks/fault_injection_v1.jsonl \
        --out runs/rollouts/rule_baseline.jsonl

    python -m training.rollout --policy vllm:/models/qwen3-8b-sft --split train \
        --episodes 4000 --temperature 1.0 --out data/rollouts/ppo_seed.jsonl

Episodes advance in lockstep: one batched generate call per turn for the whole
batch, rather than one call per turn per episode. That is what makes vLLM
rollouts worth the engine startup, and it is also how the PPO/OPD inner loop
collects experience.

Scripted policies keep per-episode state, so they get their own instance per
episode and run sequentially; the driver decides this from `policy.stateful`
instead of the caller having to remember.

`--resume` skips episodes already present in the output file, so a long rollout
survives a preempted instance.
"""

from __future__ import annotations

import argparse
import copy
import json
import time
from collections import Counter
from pathlib import Path
from typing import Iterable, Sequence

from agent_env.protocol import ActionRegistry

from .data.scenario_bank import ScenarioSpec, load_specs, sample_scenarios
from .envs import EpisodeRecord, MultiTurnDeliveryEnv
from .policies.base import BasePolicy, GenerationConfig


def existing_episode_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:  # a truncated final line from a killed run
                continue
            if record.get("record") == "episode":
                done.add(str(record.get("episode_id")))
    return done


def rollout(
    policy: BasePolicy,
    specs: Sequence[ScenarioSpec],
    *,
    concurrency: int = 32,
    max_history_turns: int | None = None,
    max_turns: int | None = None,
    registry: ActionRegistry | None = None,
    source: str | None = None,
    on_episode=None,
    verbose: bool = True,
) -> list[EpisodeRecord]:
    """Run `policy` over `specs`, returning one record per episode."""
    registry = registry or ActionRegistry.load()
    source = source or f"rollout:{policy.name}"
    records: list[EpisodeRecord] = []
    started = time.time()

    def make_env(spec: ScenarioSpec) -> MultiTurnDeliveryEnv:
        env = MultiTurnDeliveryEnv(
            spec.to_scenario(),
            seed=spec.seed,
            registry=registry,
            max_history_turns=max_history_turns,
            max_turns=max_turns,
            source=source,
            episode_id=spec.scenario_id,
        )
        env.reset()
        return env

    def finish(env: MultiTurnDeliveryEnv) -> None:
        record = env.record()
        records.append(record)
        if on_episode is not None:
            on_episode(record)
        if verbose and len(records) % 50 == 0:
            rate = len(records) / max(time.time() - started, 1e-6)
            print(f"  {len(records)}/{len(specs)} episodes ({rate:.1f} eps/s)")

    if policy.stateful:
        for spec in specs:
            episode_policy = copy.deepcopy(policy)
            episode_policy.reset()
            env = make_env(spec)
            done = False
            while not done:
                _, done, _ = env.step(episode_policy.act(env.messages))
            finish(env)
        return records

    queue = list(specs)
    active: list[MultiTurnDeliveryEnv] = []
    while queue or active:
        while queue and len(active) < concurrency:
            active.append(make_env(queue.pop(0)))
        texts = policy.act_batch([env.messages for env in active])
        still_active: list[MultiTurnDeliveryEnv] = []
        for env, text in zip(active, texts):
            _, done, _ = env.step(text)
            if done:
                finish(env)
            else:
                still_active.append(env)
        active = still_active
    return records


def write_records(path: Path, records: Iterable[EpisodeRecord], *, append: bool = False, include_messages: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    written = 0
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        for record in records:
            for line in record.to_jsonl_records(include_messages=include_messages):
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            written += 1
    return written


def quick_summary(records: Sequence[EpisodeRecord]) -> dict:
    """Outcome mix only; the full metric set lives in `training.evaluate`."""
    if not records:
        return {"episodes": 0}
    summaries = [record.summary for record in records]
    return {
        "episodes": len(records),
        "outcomes": dict(sorted(Counter(record.outcome for record in records).items())),
        "success_rate": round(sum(1 for r in records if r.outcome == "success") / len(records), 4),
        "recovered_rate": round(sum(1 for s in summaries if s.get("recovered")) / len(records), 4),
        "invalid_actions": sum(int(s.get("invalid_actions", 0)) for s in summaries),
        "malformed_actions": sum(int(s.get("malformed_actions", 0)) for s in summaries),
        "safety_violations": sum(int(s.get("safety_violations", 0)) for s in summaries),
        "mean_turns": round(sum(int(s.get("turns", 0)) for s in summaries) / len(records), 2),
    }


def resolve_specs(args: argparse.Namespace) -> list[ScenarioSpec]:
    if args.benchmark:
        specs = load_specs(args.benchmark)
        return specs[: args.episodes] if args.episodes else specs
    return sample_scenarios(args.split, args.episodes or 1000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--policy", default="rules", help="expert | rules | rules:full | hf:<path> | vllm:<path>")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, default=None, help="Score a frozen scenario file instead of sampling.")
    parser.add_argument("--split", default="train", choices=("train", "dev"))
    parser.add_argument("--episodes", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--max-history-turns", type=int, default=None)
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--repair-attempts", type=int, default=0, help="Leave at 0 for benchmarks.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--include-messages", action="store_true", help="Store full chat history per episode.")
    args = parser.parse_args()

    from .policies.llm_policy import load_policy

    generation = GenerationConfig(
        max_new_tokens=args.max_new_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
        seed=args.seed,
    )
    kwargs = {} if args.policy in ("expert",) or args.policy.startswith("rules") else {"repair_attempts": args.repair_attempts}
    policy = load_policy(args.policy, generation, **kwargs)

    specs = resolve_specs(args)
    if args.resume:
        done = existing_episode_ids(args.out)
        specs = [spec for spec in specs if spec.scenario_id not in done]
        print(f"resuming: {len(done)} episodes already logged, {len(specs)} remaining")

    print(f"rolling out {len(specs)} episodes with policy={args.policy}")
    handle_append = args.resume and args.out.exists()
    records = rollout(
        policy,
        specs,
        concurrency=args.concurrency,
        max_history_turns=args.max_history_turns,
        max_turns=args.max_turns,
    )
    write_records(args.out, records, append=handle_append, include_messages=args.include_messages)

    summary = quick_summary(records)
    summary["policy"] = args.policy
    summary["source"] = str(args.benchmark) if args.benchmark else f"split:{args.split}"
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
