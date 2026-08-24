"""The rollout driver: lockstep batching, per-episode state, resume."""

from __future__ import annotations

import json

from training.data.scenario_bank import sample_scenarios
from training.envs import episode_from_records
from training.policies import ExpertPolicy, RuleBaselinePolicy
from training.policies.base import BasePolicy
from training.rollout import existing_episode_ids, quick_summary, rollout, write_records


class _BatchSpy(BasePolicy):
    """Stateless policy that always terminates, recording the batch sizes it saw."""

    name = "spy"

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def act_batch(self, batch):
        self.batch_sizes.append(len(batch))
        return ['{"skill":"mission","action":"terminate","args":{"status":"failure","reason":"stub"},"reason":"stub"}'] * len(batch)


def test_stateless_policy_is_called_once_per_batched_turn() -> None:
    specs = sample_scenarios("dev", 8)
    policy = _BatchSpy()
    records = rollout(policy, specs, concurrency=4, verbose=False)
    assert len(records) == 8
    assert policy.batch_sizes == [4, 4]  # two full batches, one turn each


def test_concurrency_refills_as_episodes_finish() -> None:
    specs = sample_scenarios("dev", 10)
    policy = _BatchSpy()
    rollout(policy, specs, concurrency=3, verbose=False)
    assert max(policy.batch_sizes) <= 3
    assert sum(policy.batch_sizes) == 10


def test_stateful_policies_get_a_fresh_instance_per_episode() -> None:
    """A shared rule policy would carry retry counters across episodes."""
    specs = sample_scenarios("dev", 6)
    policy = RuleBaselinePolicy()
    records = rollout(policy, specs, verbose=False)
    assert len(records) == 6
    assert policy.detect_failures == 0 and policy.grasp_failures == 0  # the template was never mutated


def test_expert_rollout_recovers_everywhere() -> None:
    records = rollout(ExpertPolicy(), sample_scenarios("dev", 30), verbose=False)
    assert all(record.summary["recovered"] for record in records)
    assert quick_summary(records)["invalid_actions"] == 0


def test_records_survive_the_write_read_roundtrip(tmp_path) -> None:
    records = rollout(ExpertPolicy(), sample_scenarios("dev", 4), verbose=False)
    path = tmp_path / "rollout.jsonl"
    assert write_records(path, records) == 4
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    restored = episode_from_records(lines)
    assert len(restored) == 4
    assert {record.episode_id for record in restored} == {record.episode_id for record in records}


def test_resume_skips_already_logged_episodes(tmp_path) -> None:
    specs = sample_scenarios("dev", 6)
    path = tmp_path / "rollout.jsonl"
    first = rollout(ExpertPolicy(), specs[:3], verbose=False)
    write_records(path, first)

    done = existing_episode_ids(path)
    assert done == {record.episode_id for record in first}
    remaining = [spec for spec in specs if spec.scenario_id not in done]
    assert len(remaining) == 3

    write_records(path, rollout(ExpertPolicy(), remaining, verbose=False), append=True)
    assert len(existing_episode_ids(path)) == 6


def test_resume_tolerates_a_truncated_final_line(tmp_path) -> None:
    path = tmp_path / "rollout.jsonl"
    records = rollout(ExpertPolicy(), sample_scenarios("dev", 2), verbose=False)
    write_records(path, records)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"record": "step", "episode_id": "ep-trunc')  # killed mid-write
    assert len(existing_episode_ids(path)) == 2


def test_quick_summary_reports_outcome_mix() -> None:
    records = rollout(_BatchSpy(), sample_scenarios("dev", 5), verbose=False)
    summary = quick_summary(records)
    assert summary["episodes"] == 5
    assert summary["outcomes"]["wrong_termination"] == 5  # gave up on recoverable missions
    assert summary["success_rate"] == 0.0


def test_max_turns_truncates_a_runaway_episode() -> None:
    class _Stall(BasePolicy):
        name = "stall"

        def act_batch(self, batch):
            return ['{"skill":"system-state","action":"query_state","args":{},"reason":"look"}'] * len(batch)

    records = rollout(_Stall(), sample_scenarios("dev", 2), max_turns=5, verbose=False)
    assert all(len(record.turns) == 5 for record in records)
