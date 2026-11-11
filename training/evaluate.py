#!/usr/bin/env python3
"""Evaluation: the metric set, the failure taxonomy, and the bucket breakdowns.

    python -m training.evaluate --policy rules --benchmark benchmarks/fault_injection_v1.jsonl \
        --out runs/eval/rule_baseline.json --markdown

    python -m training.evaluate --rollout runs/rollouts/ppo_progress.jsonl --out runs/eval/ppo.json

A single success rate cannot separate "the model stopped too early" from "the
model tried something illegal" from "the model looped until the step limit", and
those are exactly the differences this project is trying to attribute to
different supervision sources. So every run reports:

    task success            delivered and terminated correctly
    recovery success        correct outcome on fault-injected episodes only
    invalid skill rate      illegal or unparseable calls / total calls
    safety violation rate   motion without authorization, handover without
                            confirmation, motion after a cancel
    mean high-level actions turns per episode
    repeated call rate      identical call re-issued within the last 3 turns
    no-progress ratio       episodes that stalled for >= 2 consecutive turns

plus a mutually exclusive terminal failure class per episode (premature stop,
false success, wrong cancel, step limit) and non-exclusive behavioural flags,
bucketed by fault family, failing phase and trajectory length.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from .envs import EpisodeRecord, episode_from_records

REPEAT_WINDOW = 3
#: A genuine stall, not a fault the policy handled. Calibrated on the benchmark:
#: the scripted expert never exceeds two consecutive non-advancing turns, so
#: counting at two would report a 26% stall rate for a policy that recovers from
#: everything. See `RewardConfig.no_progress_threshold`.
NO_PROGRESS_THRESHOLD = 3
CORRECT_OUTCOMES = ("success", "correct_abort", "cancelled_ok")

#: Mutually exclusive terminal classes.
FAILURE_CLASSES = (
    "success",
    "correct_abort",
    "cancelled_ok",
    "premature_stop",
    "false_success",
    "wrong_cancel",
    "step_limit",
    "unfinished",
)

#: Non-exclusive behavioural flags, tracked alongside the terminal class.
BEHAVIOUR_FLAGS = ("invalid_recovery", "observation_misuse", "malformed_output", "safety_violation", "stalled")


def _canonical(action: dict[str, Any]) -> str:
    return json.dumps(
        {"skill": action.get("skill"), "action": action.get("action"), "args": action.get("args") or {}},
        sort_keys=True,
        ensure_ascii=False,
    )


def terminal_class(record: EpisodeRecord) -> str:
    """One label per episode describing how it ended."""
    outcome = record.outcome
    if outcome in ("success", "correct_abort", "cancelled_ok"):
        return outcome
    if outcome == "step_limit":
        return "step_limit"
    if outcome == "wrong_termination":
        final = record.turns[-1].action if record.turns else {}
        status = (final.get("args") or {}).get("status")
        if status == "success":
            return "false_success"  # claimed delivery that never happened
        if status == "cancelled":
            return "wrong_cancel"
        return "premature_stop"  # gave up while the situation was still recoverable
    return "unfinished"


def behaviour_flags(record: EpisodeRecord) -> set[str]:
    flags: set[str] = set()
    if any(turn.invalid for turn in record.turns):
        flags.add("invalid_recovery")
    if any(turn.malformed for turn in record.turns):
        flags.add("malformed_output")
    if int(record.summary.get("safety_violations", 0)):
        flags.add("safety_violation")
    if max((int(turn.info.get("no_progress_count", 0)) for turn in record.turns), default=0) >= NO_PROGRESS_THRESHOLD:
        flags.add("stalled")
    for turn in record.turns:
        result = turn.result or {}
        if result.get("error_code") == "PRECONDITION_REJECTED" and "detection" in str(result.get("message", "")):
            flags.add("observation_misuse")
            break
    return flags


def failing_phase(record: EpisodeRecord) -> str:
    """Phase the episode was in when it went wrong (or `done` when it succeeded)."""
    if record.outcome == "success":
        return "done"
    for turn in reversed(record.turns):
        phase = turn.info.get("phase")
        if phase and phase != "DONE":
            return str(phase)
    return "IDLE"


def length_bucket(turns: int) -> str:
    if turns <= 8:
        return "short(<=8)"
    if turns <= 14:
        return "medium(9-14)"
    return "long(>=15)"


def fault_bucket(record: EpisodeRecord) -> str:
    faults = record.scenario.get("faults") or []
    if not faults:
        return "clean"
    if len(faults) > 1:
        return "mixed"
    from agent_env.faults import FaultType
    from training.data.scenario_bank import _FAULT_BUCKET  # noqa: PLC0415 - avoids a cycle at import time

    return _FAULT_BUCKET.get(FaultType(faults[0]["fault"]), "other")


def fault_names(record: EpisodeRecord) -> list[str]:
    return sorted(item["fault"] for item in record.scenario.get("faults") or [])


@dataclass
class EpisodeMetrics:
    episode_id: str
    outcome: str
    terminal_class: str
    flags: list[str]
    turns: int
    invalid_calls: int
    malformed_calls: int
    safety_violations: int
    repeated_calls: int
    max_no_progress: int
    faulted: bool
    fault_bucket: str
    faults: list[str]
    failing_phase: str
    length_bucket: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def episode_metrics(record: EpisodeRecord) -> EpisodeMetrics:
    seen: list[str] = []
    repeated = 0
    for turn in record.turns:
        signature = _canonical(turn.action)
        if signature in seen[-REPEAT_WINDOW:]:
            repeated += 1
        seen.append(signature)

    faults = fault_names(record)
    return EpisodeMetrics(
        episode_id=record.episode_id,
        outcome=record.outcome,
        terminal_class=terminal_class(record),
        flags=sorted(behaviour_flags(record)),
        turns=len(record.turns),
        invalid_calls=sum(1 for turn in record.turns if turn.invalid),
        malformed_calls=sum(1 for turn in record.turns if turn.malformed),
        safety_violations=int(record.summary.get("safety_violations", 0)),
        repeated_calls=repeated,
        max_no_progress=max((int(turn.info.get("no_progress_count", 0)) for turn in record.turns), default=0),
        faulted=bool(faults),
        fault_bucket=fault_bucket(record),
        faults=faults,
        failing_phase=failing_phase(record),
        length_bucket=length_bucket(len(record.turns)),
    )


@dataclass
class Metrics:
    episodes: int = 0
    task_success: float = 0.0
    correct_outcome: float = 0.0
    recovery_success: float = 0.0
    faulted_episodes: int = 0
    invalid_skill_rate: float = 0.0
    malformed_rate: float = 0.0
    safety_violation_rate: float = 0.0
    mean_actions: float = 0.0
    repeated_call_rate: float = 0.0
    no_progress_ratio: float = 0.0
    total_calls: int = 0
    terminal_classes: dict[str, int] = field(default_factory=dict)
    flags: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate(metrics: Sequence[EpisodeMetrics]) -> Metrics:
    if not metrics:
        return Metrics()
    count = len(metrics)
    calls = sum(item.turns for item in metrics)
    faulted = [item for item in metrics if item.faulted]
    return Metrics(
        episodes=count,
        task_success=round(sum(1 for item in metrics if item.outcome == "success") / count, 4),
        correct_outcome=round(sum(1 for item in metrics if item.outcome in CORRECT_OUTCOMES) / count, 4),
        recovery_success=round(
            sum(1 for item in faulted if item.outcome in CORRECT_OUTCOMES) / len(faulted), 4
        )
        if faulted
        else 0.0,
        faulted_episodes=len(faulted),
        invalid_skill_rate=round(sum(item.invalid_calls for item in metrics) / max(calls, 1), 4),
        malformed_rate=round(sum(item.malformed_calls for item in metrics) / max(calls, 1), 4),
        safety_violation_rate=round(sum(1 for item in metrics if item.safety_violations) / count, 4),
        mean_actions=round(calls / count, 2),
        repeated_call_rate=round(sum(item.repeated_calls for item in metrics) / max(calls, 1), 4),
        no_progress_ratio=round(sum(1 for item in metrics if item.max_no_progress >= NO_PROGRESS_THRESHOLD) / count, 4),
        total_calls=calls,
        terminal_classes=dict(sorted(Counter(item.terminal_class for item in metrics).items())),
        flags=dict(sorted(Counter(flag for item in metrics for flag in item.flags).items())),
    )


def bucketed(metrics: Sequence[EpisodeMetrics], key: Callable[[EpisodeMetrics], str]) -> dict[str, Metrics]:
    groups: dict[str, list[EpisodeMetrics]] = defaultdict(list)
    for item in metrics:
        groups[key(item)].append(item)
    return {name: aggregate(rows) for name, rows in sorted(groups.items())}


class _StepResult:
    """Adapter so the contract's own mapping can read an L1 result dict.

    The L1 environment records a result as a dictionary; the contract's
    `receipt_from_step_result` reads attributes. Rather than duplicating the
    mapping here -- and letting the two drift -- this hands the contract the
    shape it expects.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.status = str(payload.get("status", "none"))
        self.error_code = str(payload.get("error_code", "OK"))
        self.message = str(payload.get("message", ""))


def receipts_for(record: EpisodeRecord) -> list[dict[str, Any]]:
    """One contract receipt per turn of an L1 episode.

    This is the same mapping the runtime applies when it drives L1 through the
    backend, so an L1 receipt and an L2 receipt mean the same thing -- which
    is the whole reason the contract exists.

    What this *cannot* give is the execution-layer history: an L1 trajectory has
    no ledger, so it cannot show a duplicate effect, and it carries no per-turn
    wall clock, so it cannot show latency. Those are reported as not applicable
    with the reason, rather than as zero: on this path they would be zero by
    definition, not by measurement.
    """
    from agent_harness.contracts import receipt_from_step_result

    rows: list[dict[str, Any]] = []
    for turn in record.turns:
        receipt = receipt_from_step_result(
            _StepResult(turn.result or {}),
            episode_id=record.episode_id,
            command_id=f"{record.episode_id}-{turn.turn_index}",
        )
        row = receipt.to_dict()
        # The adapter stamps a receipt with "now" when it is given no times.
        # On this path nothing was timed, so the timestamps are not carried
        # over: a latency of 0.0 ms would read as a measurement of an action
        # that took no time, when in fact nobody measured it.
        row.pop("started_at", None)
        row.pop("finished_at", None)
        skill = str((turn.action or {}).get("skill", ""))
        action = str((turn.action or {}).get("action", ""))
        row["capability"] = f"{skill}.{action}" if skill and action else "unknown"
        rows.append(row)
    return rows


def receipt_section(
    rows: Sequence[dict[str, Any]],
    *,
    source: str,
    events: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The receipt-level metrics, and what this path cannot measure.

    ``events`` are ledger event rows. Without a ledger there is nothing to count
    a duplicate effect from, so it is reported as not applicable *with the
    reason* rather than as zero: on that path the rate would be zero by
    definition, not by measurement. The same goes for latency -- a path that
    never timed anything has no latency, and reporting 0.0 ms would look like a
    measurement of an action that took no time.
    """
    from .receipt_metrics import duplicate_effect_rate, receipt_metrics

    metrics = receipt_metrics(rows)
    section: dict[str, Any] = {"source": source, **metrics}
    not_applicable: dict[str, str] = {}

    timed = any(
        row.get("started_at") is not None and row.get("finished_at") is not None for row in rows
    )
    if not timed:
        not_applicable["latency_p50_ms"] = "this path carries no per-command wall clock"
        not_applicable["latency_p95_ms"] = "this path carries no per-command wall clock"
    if events is None:
        not_applicable["duplicate_effect_rate"] = (
            "no ledger on this path: one turn is one command by construction, so the rate "
            "would be zero by definition rather than by measurement"
        )
    else:
        section["duplicate_effect_rate"] = duplicate_effect_rate(events)
    if not_applicable:
        section["not_applicable"] = not_applicable
    return section


def evaluate_records(
    records: Sequence[EpisodeRecord],
    label: str = "",
    *,
    receipts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score a set of episodes.

    ``receipts`` is a receipt section the caller built. The L2 path builds it
    from the receipts the stack actually returned; the L1 path has no such
    thing and gets one derived from the recorded trajectory. Both go through
    the same contract mapping, so an L1 receipt and an L2 receipt mean the same
    thing -- which is the whole reason the contract exists.
    """
    metrics = [episode_metrics(record) for record in records]
    section = receipts
    if section is None:
        section = receipt_section(
            [row for record in records for row in receipts_for(record)],
            source="l1_trajectory",
        )
    return {
        "label": label,
        "overall": aggregate(metrics).to_dict(),
        "by_fault_bucket": {name: value.to_dict() for name, value in bucketed(metrics, lambda item: item.fault_bucket).items()},
        "by_fault": {
            name: value.to_dict()
            for name, value in bucketed(metrics, lambda item: "+".join(item.faults) or "clean").items()
        },
        "by_failing_phase": {
            name: value.to_dict() for name, value in bucketed(metrics, lambda item: item.failing_phase).items()
        },
        "by_length": {name: value.to_dict() for name, value in bucketed(metrics, lambda item: item.length_bucket).items()},
        "episodes": [item.to_dict() for item in metrics],
        "receipts": section,
    }


def load_records(path: Path) -> list[EpisodeRecord]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return episode_from_records(rows)


def markdown_table(report: dict[str, Any], section: str = "by_fault_bucket") -> str:
    headers = ["bucket", "n", "success", "recovery", "invalid", "safety", "actions", "repeat", "stalled"]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    rows = {"overall": report["overall"], **report.get(section, {})}
    for name, metrics in rows.items():
        lines.append(
            "| {name} | {episodes} | {success:.1%} | {recovery:.1%} | {invalid:.1%} | {safety:.1%} | {actions:.1f} | {repeat:.1%} | {stalled:.1%} |".format(
                name=name,
                episodes=metrics["episodes"],
                success=metrics["task_success"],
                recovery=metrics["recovery_success"],
                invalid=metrics["invalid_skill_rate"],
                safety=metrics["safety_violation_rate"],
                actions=metrics["mean_actions"],
                repeat=metrics["repeated_call_rate"],
                stalled=metrics["no_progress_ratio"],
            )
        )
    return "\n".join(lines)


def failure_table(report: dict[str, Any]) -> str:
    overall = report["overall"]
    lines = ["| class / flag | episodes | share |", "|---|---|---|"]
    total = max(overall["episodes"], 1)
    for name, count in overall["terminal_classes"].items():
        lines.append(f"| {name} | {count} | {count / total:.1%} |")
    for name, count in overall["flags"].items():
        lines.append(f"| flag: {name} | {count} | {count / total:.1%} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--rollout", type=Path, help="Score an existing rollout JSONL.")
    source.add_argument("--policy", type=str, help="Roll out a policy first (expert | rules | hf:... | vllm:...).")
    parser.add_argument("--benchmark", type=Path, default=Path("benchmarks/fault_injection_v1.jsonl"))
    parser.add_argument("--episodes", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--save-rollout", type=Path, default=None)
    parser.add_argument("--label", type=str, default="")
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    if args.rollout:
        records = load_records(args.rollout)
        label = args.label or args.rollout.stem
    else:
        from .data.scenario_bank import load_specs
        from .policies.llm_policy import load_policy
        from .rollout import rollout, write_records

        specs = load_specs(args.benchmark)
        if args.episodes:
            specs = specs[: args.episodes]
        policy = load_policy(args.policy)
        print(f"evaluating {args.policy} on {len(specs)} held-out episodes")
        records = rollout(policy, specs, concurrency=args.concurrency, verbose=False)
        label = args.label or args.policy
        if args.save_rollout:
            write_records(args.save_rollout, records)

    report = evaluate_records(records, label)
    overall = report["overall"]
    print(json.dumps({key: value for key, value in overall.items() if key not in ("terminal_classes", "flags")}, indent=2))
    print("\nterminal classes:", json.dumps(overall["terminal_classes"], ensure_ascii=False))
    print("behaviour flags: ", json.dumps(overall["flags"], ensure_ascii=False))

    if args.markdown:
        print("\n" + markdown_table(report))
        print("\n" + failure_table(report))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"\n-> {args.out}")


if __name__ == "__main__":
    main()
