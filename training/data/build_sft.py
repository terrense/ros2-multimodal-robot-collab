#!/usr/bin/env python3
"""Build the SFT corpus from expert / repaired trajectories.

    python -m training.data.build_sft --episodes 8000 --out data/sft

Two granularities:

    episode   one sample per episode, every assistant turn supervised.
              Teaches the whole recovery arc in context; what SFT trains on here.
    turn      one sample per decision, prompt = (optionally truncated) history.
              Useful for short-context runs and for rebalancing rare recoveries.

Only episodes the environment scored as recovered, with zero illegal actions and
zero safety violations, enter the corpus. `--from-trajectories` reuses an
existing rollout file instead of the scripted expert, which is how
rejection-sampled SFT rounds are built from a trained policy's own successes.

The train/val split is by scenario, never by sample: two turns of the same
episode must not land on opposite sides of the split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from agent_env.protocol import ActionRegistry

from ..chat_format import build_messages, system_prompt
from ..envs import EpisodeRecord, episode_from_records, run_episode, scripted_policy
from .scenario_bank import ScenarioSpec, bucket_of, sample_scenarios

GRANULARITIES = ("episode", "turn")


@dataclass
class Sample:
    messages: list[dict[str, str]]
    meta: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"messages": self.messages, "meta": self.meta}, ensure_ascii=False)

    def key(self) -> str:
        blob = json.dumps(self.messages, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def episode_sample(record: EpisodeRecord, system: str) -> Sample:
    messages = list(record.messages)
    if messages and messages[0]["role"] == "system":
        messages[0] = {"role": "system", "content": system}
    return Sample(
        messages=messages,
        meta={
            "episode_id": record.episode_id,
            "granularity": "episode",
            "outcome": record.outcome,
            "bucket": _bucket(record),
            "turns": len(record.turns),
            "recovery_turns": sum(1 for turn in record.turns if _is_recovery_turn(turn.observation)),
            "faults": sorted(item["fault"] for item in record.scenario.get("faults") or []),
        },
    )


def turn_samples(record: EpisodeRecord, system: str, max_history_turns: int | None) -> list[Sample]:
    steps = [
        {"observation": turn.observation, "action": turn.action, "assistant_text": turn.assistant_text}
        for turn in record.turns
    ]
    samples: list[Sample] = []
    for index, turn in enumerate(record.turns):
        history = build_messages(steps[:index], system=system, max_history_turns=max_history_turns)
        history.append({"role": "user", "content": _observation_message(steps[index]["observation"], index == 0)})
        history.append({"role": "assistant", "content": turn.assistant_text})
        samples.append(
            Sample(
                messages=history,
                meta={
                    "episode_id": record.episode_id,
                    "granularity": "turn",
                    "turn_index": turn.turn_index,
                    "outcome": record.outcome,
                    "bucket": _bucket(record),
                    "phase": turn.info.get("phase"),
                    "error_code": (turn.observation.get("last_result") or {}).get("error_code"),
                    "recovery_turn": _is_recovery_turn(turn.observation),
                },
            )
        )
    return samples


def _observation_message(observation: dict[str, Any], first: bool) -> str:
    from ..chat_format import observation_content

    return observation_content(observation, include_brief=first)


def _is_recovery_turn(observation: dict[str, Any]) -> bool:
    """A decision made right after something went wrong; the turns this project cares about."""
    result = observation.get("last_result") or {}
    return bool(
        result.get("error_code") not in (None, "OK")
        or result.get("status") in ("failed", "rejected")
        or int(observation.get("no_progress_count", 0)) >= 1
        or observation.get("safety_events")
    )


def _bucket(record: EpisodeRecord) -> str:
    from agent_env.faults import FaultSpec

    faults = [FaultSpec.from_dict(item) for item in record.scenario.get("faults") or []]
    return bucket_of(faults)


def collect_expert_records(specs: Iterable[ScenarioSpec], registry: ActionRegistry, verbose: bool = True) -> list[EpisodeRecord]:
    records: list[EpisodeRecord] = []
    specs = list(specs)
    for index, spec in enumerate(specs, start=1):
        records.append(
            run_episode(
                scripted_policy(),
                spec.to_scenario(),
                seed=spec.seed,
                registry=registry,
                source="l1_expert",
                episode_id=spec.scenario_id,
            )
        )
        if verbose and index % 1000 == 0:
            print(f"  rolled out {index}/{len(specs)} episodes")
    return records


def keep(record: EpisodeRecord, allow_step_limit: bool = False) -> bool:
    summary = record.summary
    if summary.get("invalid_actions", 0) or summary.get("malformed_actions", 0):
        return False
    if summary.get("safety_violations", 0):
        return False
    if not allow_step_limit and record.outcome == "step_limit":
        return False
    return bool(summary.get("recovered"))


def write_jsonl(path: Path, samples: Iterable[Sample]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for sample in samples:
            handle.write(sample.to_json() + "\n")
            count += 1
    return count


def stats(samples: list[Sample]) -> dict[str, Any]:
    turns = [len([m for m in sample.messages if m["role"] == "assistant"]) for sample in samples]
    chars = [sum(len(message["content"]) for message in sample.messages) for sample in samples]
    return {
        "samples": len(samples),
        "buckets": dict(sorted(Counter(sample.meta.get("bucket") for sample in samples).items())),
        "outcomes": dict(sorted(Counter(sample.meta.get("outcome") for sample in samples).items())),
        # Turn samples carry a boolean, episode samples a count; both roll up here.
        "recovery_turns": sum(
            int(sample.meta.get("recovery_turns", 0)) + int(bool(sample.meta.get("recovery_turn")))
            for sample in samples
        ),
        "assistant_turns_total": sum(turns),
        "assistant_turns_mean": round(sum(turns) / max(len(turns), 1), 2),
        "chars_mean": round(sum(chars) / max(len(chars), 1), 1),
        "chars_p95": sorted(chars)[int(0.95 * (len(chars) - 1))] if chars else 0,
        "approx_tokens_mean": round(sum(chars) / max(len(chars), 1) / 3.5, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("data/sft"))
    parser.add_argument("--episodes", type=int, default=8000)
    parser.add_argument("--granularity", choices=GRANULARITIES, default="episode")
    parser.add_argument("--max-history-turns", type=int, default=None, help="Turn granularity only.")
    parser.add_argument("--val-fraction", type=float, default=0.02)
    parser.add_argument("--split", default="train", help="Scenario split to draw from (never 'benchmark').")
    parser.add_argument("--from-trajectories", type=Path, default=None, help="Reuse a rollout JSONL instead of the expert.")
    parser.add_argument("--allow-step-limit", action="store_true")
    parser.add_argument("--seed", type=int, default=17)
    args = parser.parse_args()

    if args.split == "benchmark":
        raise SystemExit("refusing to build training data from the held-out benchmark split")

    registry = ActionRegistry.load()
    system = system_prompt(registry)

    if args.from_trajectories:
        records = episode_from_records(
            [json.loads(line) for line in args.from_trajectories.read_text(encoding="utf-8").splitlines() if line.strip()]
        )
        print(f"loaded {len(records)} episodes from {args.from_trajectories}")
    else:
        specs = sample_scenarios(args.split, args.episodes)
        print(f"rolling out {len(specs)} expert episodes on split={args.split}")
        records = collect_expert_records(specs, registry)

    kept = [record for record in records if keep(record, args.allow_step_limit)]
    print(f"kept {len(kept)}/{len(records)} episodes after quality filtering")

    samples: list[Sample] = []
    for record in kept:
        if args.granularity == "episode":
            samples.append(episode_sample(record, system))
        else:
            samples.extend(turn_samples(record, system, args.max_history_turns))

    seen: set[str] = set()
    unique: list[Sample] = []
    for sample in samples:
        key = sample.key()
        if key in seen:
            continue
        seen.add(key)
        unique.append(sample)
    print(f"{len(unique)}/{len(samples)} samples after exact-duplicate removal")

    # Split by episode so no episode straddles train and val.
    episode_ids = sorted({sample.meta["episode_id"] for sample in unique})
    random.Random(args.seed).shuffle(episode_ids)
    val_count = max(1, int(len(episode_ids) * args.val_fraction)) if args.val_fraction > 0 else 0
    val_ids = set(episode_ids[:val_count])

    train_samples = [sample for sample in unique if sample.meta["episode_id"] not in val_ids]
    val_samples = [sample for sample in unique if sample.meta["episode_id"] in val_ids]

    train_path = args.out / "train.jsonl"
    val_path = args.out / "val.jsonl"
    write_jsonl(train_path, train_samples)
    write_jsonl(val_path, val_samples)

    report = {
        "granularity": args.granularity,
        "source": str(args.from_trajectories) if args.from_trajectories else f"scripted_expert:{args.split}",
        "episodes_rolled_out": len(records),
        "episodes_kept": len(kept),
        "duplicates_removed": len(samples) - len(unique),
        "train": stats(train_samples),
        "val": stats(val_samples),
    }
    (args.out / "stats.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    print(f"train: {len(train_samples)} -> {train_path}")
    print(f"val:   {len(val_samples)} -> {val_path}")
    print(json.dumps(report["train"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
