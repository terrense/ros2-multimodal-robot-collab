#!/usr/bin/env python3
"""On-policy distillation, and the OPD -> PPO handover.

    # pure OPD: teacher guidance only, no environment reward
    deepspeed --num_gpus 4 -m training.train_opd --config training/configs/opd_pure.yaml

    # hybrid: dense teacher guidance early, environment reward late
    deepspeed --num_gpus 4 -m training.train_opd --config training/configs/opd_warmup_ppo.yaml

The student rolls out on its own. On gated turns -- an error code, repeated
no-progress, a safety event, high student entropy -- a frozen teacher scores the
tokens the student actually sampled, and the reverse KL on those tokens becomes
the learning signal. The teacher never supplies trajectories, so the supervision
lives on the state distribution the student really reaches, which is what SFT on
teacher trajectories cannot provide.

A note on what the gate saves. In `turn` sequence mode each turn is its own
sequence, so a skipped turn is a teacher forward pass not paid for. In `episode`
mode one teacher pass covers the whole episode, so the gate concentrates the
*signal* rather than cutting the cost -- except for episodes where nothing is
gated in at all, which are skipped entirely.

The hybrid schedule is the experiment: `mix.opd_start/opd_end` and
`mix.env_start/env_end` decide how fast the teacher hands over to the simulator.
The claim being tested is that the teacher fixes policy-level failures
(premature stop, invalid recovery, observation misuse) while only environment
reward fixes final task success and real skill executability.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .config import OPDTrainConfig, add_config_args, load_config, save_config
from .data.scenario_bank import sample_scenarios
from .envs import EpisodeRecord
from .policies.teacher import (
    GateConfig,
    MixSchedule,
    OPDConfig,
    TurnGate,
    distillation_weight,
    features_from_turn,
)
from .rollout import quick_summary, rollout
from .train_ppo import PPORunner, reward_config_from
from .trainers.common import MetricLogger, is_main_process, save_checkpoint
from .trainers.experience import (
    EpisodeExperience,
    attach_credit,
    build_experience,
    summarize,
    whiten_batch,
)
from .trainers.opd import (
    MixedLossConfig,
    build_turn_weight_tensor,
    mixed_loss,
    opd_loss,
    teacher_turn_rewards,
)
from .trainers.ppo import (
    combined_loss,
    entropy_from_logits,
    gather_logprobs,
    shift_mask,
)


def gate_config_from(config: OPDTrainConfig) -> GateConfig:
    teacher = config.teacher
    return GateConfig(
        on_error=teacher.on_error,
        on_safety_event=teacher.on_safety_event,
        no_progress_threshold=teacher.no_progress_threshold,
        entropy_threshold=teacher.entropy_threshold,
        disagreement_threshold=teacher.disagreement_threshold,
        random_sample_prob=teacher.random_sample_prob,
        max_scored_fraction=teacher.max_scored_fraction,
        max_scored_turns=teacher.max_scored_turns,
    )


def _world_size() -> int:
    """How many ranks this run has.

    Read from torch when the process group is up, and from the launcher
    environment otherwise -- `deepspeed --num_gpus 4` sets both, but the
    environment is there before torch initialises.
    """
    import os

    try:
        import torch.distributed as distributed

        if distributed.is_initialized():
            return distributed.get_world_size()
    except Exception:  # pragma: no cover - torch may be absent
        pass
    return int(os.environ.get("WORLD_SIZE", "1") or 1)


class OPDRunner(PPORunner):
    """PPO runner plus a frozen teacher and the mixing schedule."""

    def __init__(self, config: OPDTrainConfig) -> None:
        # The OPD-level model / rollout / reward / credit / run blocks are
        # authoritative; the nested `ppo:` block only supplies PPO-specific
        # hyperparameters (clipping, value coefficient, KL, critic mode).
        config.ppo.model = config.model
        config.ppo.optim = config.optim
        config.ppo.run = config.run
        config.ppo.rollout = config.rollout
        config.ppo.reward = config.reward
        config.ppo.credit = config.credit
        config.ppo.eval_episodes = config.eval_episodes
        super().__init__(config.ppo)

        self.opd_config = config
        self.output_dir = Path(config.run.output_dir)
        self.logger = MetricLogger(
            self.output_dir / "metrics.jsonl",
            keys=(
                "iteration",
                "rollout/success_rate",
                "teacher/scored_fraction",
                "opd/reverse_kl",
                "mix/opd_weight",
                "mix/env_weight",
            ),
        )
        self.reward_config = reward_config_from(config)
        self.sequence_mode = "turn" if config.rollout.max_history_turns is not None else "episode"
        self.gate_config = gate_config_from(config)
        self.weight_config = OPDConfig(
            base_weight=config.teacher.base_weight,
            uncertainty_scale=config.teacher.uncertainty_scale,
            disagreement_scale=config.teacher.disagreement_scale,
            max_weight=config.teacher.max_weight,
        )
        self.schedule = MixSchedule(
            total_steps=config.iterations,
            opd_start=config.mix.opd_start,
            opd_end=config.mix.opd_end,
            env_start=config.mix.env_start,
            env_end=config.mix.env_end,
            warmup_fraction=config.mix.warmup_fraction,
            mode=config.mix.mode,
        )
        self.rng = random.Random(config.run.seed)
        self.gate_stats: dict[str, int] = {}

    # ------------------------------------------------------------------ setup

    def setup(self) -> None:
        super().setup()
        self.teacher = self.build_teacher()

    def build_teacher(self) -> Any:
        """Put the teacher where it fits.

        The teacher is 30B -- 60.8 GB in bf16 -- and this process already holds
        the student, its gradients and its optimizer state on these cards. One
        copy per rank cannot fit next to them, so the default is a separate
        server process that the ranks query over HTTP.
        """
        from .policies.teacher import (
            RemoteTeacherScorer,
            TeacherPlacementError,
            TeacherScorer,
            spread_max_memory,
        )

        teacher = self.opd_config.teacher
        if teacher.backend == "server":
            return RemoteTeacherScorer(
                teacher.base_url,
                model=teacher.model or teacher.path,
                api_key=teacher.api_key,
                timeout_s=teacher.timeout_s,
                config=self.weight_config,
                tokenizer=self.tokenizer,  # same family, so student token ids address the same text
            )

        if teacher.backend != "local":
            raise TeacherPlacementError(f"unknown teacher.backend {teacher.backend!r}")

        world_size = _world_size()
        if world_size > 1 and not teacher.allow_multi_rank_local:
            raise TeacherPlacementError(
                f"the teacher is {teacher.params_billion}B "
                f"(~{teacher.params_billion * 2:.0f} GB in bf16) and this run has {world_size} ranks. "
                "One copy per rank puts that many copies on the same cards as the student and "
                "OOMs. Run the teacher as its own server (bash training/scripts/serve_teacher.sh) "
                "and set teacher.backend=server with teacher.base_url, or set "
                "teacher.allow_multi_rank_local=true if you really mean it."
            )
        return TeacherScorer(
            teacher.path,
            dtype=teacher.dtype,
            config=self.weight_config,
            tokenizer=self.tokenizer,
            max_memory=spread_max_memory(teacher.max_memory_fraction),
        )

    # -------------------------------------------------------------- rollout

    def collect(self, iteration: int) -> list[EpisodeRecord]:
        rollout_config = self.opd_config.rollout
        specs = sample_scenarios(
            rollout_config.split,
            rollout_config.episodes_per_iter,
            start_index=iteration * rollout_config.episodes_per_iter,
        )
        return rollout(
            self.rollout_policy,
            specs,
            concurrency=rollout_config.concurrency,
            max_history_turns=rollout_config.max_history_turns,
            max_turns=rollout_config.max_turns,
            source=f"opd_iter_{iteration}",
            verbose=False,
        )

    # ------------------------------------------------------- teacher scoring

    def student_span_stats(self, sequence: Any) -> tuple[list[float], list[float]]:
        """Mean log-prob and mean entropy of the student on each of its own spans."""
        import torch

        input_ids = torch.tensor([sequence.input_ids], device=self.device)
        with torch.no_grad():
            logits = self.actor(input_ids=input_ids).logits
            logprobs = gather_logprobs(logits, input_ids)[0]
            entropies = entropy_from_logits(logits)[0]
        span_logprobs, span_entropies = [], []
        for start, end in sequence.spans:
            lo, hi = max(0, start - 1), max(0, end - 1)
            span_logprobs.append(float(logprobs[lo:hi].mean().item()) if hi > lo else 0.0)
            span_entropies.append(float(entropies[lo:hi].mean().item()) if hi > lo else 0.0)
        return span_logprobs, span_entropies

    def score_with_teacher(self, experience: EpisodeExperience, record: EpisodeRecord) -> list[float]:
        """Gate the turns, score the gated ones, and attach the signal in place.

        Returns the per-turn teacher reward (used only by the `reward` OPD path).
        """
        gate = TurnGate(self.gate_config, rng=self.rng)
        turn_rewards = [0.0] * len(experience.turns)
        previous_disagreement = 0.0

        for sequence in experience.sequences:
            student_logprobs, student_entropies = self.student_span_stats(sequence)
            decisions = []
            for position, turn_index in enumerate(sequence.turn_indices):
                turn_record = next((turn for turn in record.turns if turn.turn_index == turn_index), None)
                features = features_from_turn(
                    turn_record if turn_record is not None else {"turn_index": turn_index},
                    student_entropy=student_entropies[position],
                    prev_disagreement=previous_disagreement,
                )
                decision = gate.decide(features)
                self.gate_stats[decision.reason] = self.gate_stats.get(decision.reason, 0) + 1
                decisions.append(decision)

            if not any(decision.scored for decision in decisions):
                continue  # no teacher pass for a sequence with nothing worth asking about

            distillations = self.teacher.score_spans(
                sequence.input_ids,
                sequence.spans,
                student_logprobs,
                student_entropies,
                [decision.reason for decision in decisions],
                sequence.turn_indices,
            )

            teacher_per_token = self.teacher.token_logprobs(sequence.input_ids)

            position_of = {turn.turn_index: index for index, turn in enumerate(experience.turns)}
            weights: list[float] = []
            scored_items = []
            for position, decision in enumerate(decisions):
                item = distillations[position]
                if not decision.scored:
                    weights.append(0.0)
                    continue
                item.weight = distillation_weight(item.student_entropy, item.disagreement, self.weight_config)
                weights.append(item.weight)
                previous_disagreement = item.disagreement
                turn_position = position_of.get(item.turn_index)
                if turn_position is not None:
                    experience.turns[turn_position].teacher_weight = item.weight
                    experience.turns[turn_position].gate_reason = decision.reason
                scored_items.append(item)

            if self.opd_config.opd_loss == "reward" and scored_items:
                for position, bonus in enumerate(
                    teacher_turn_rewards(scored_items, len(experience.turns), positions=position_of)
                ):
                    turn_rewards[position] += bonus

            padded = np.zeros(sequence.length, dtype=np.float64)
            padded[1:] = teacher_per_token[: sequence.length - 1]
            sequence.teacher_logprobs = padded
            sequence.teacher_weights = (
                build_turn_weight_tensor(sequence.spans, weights, sequence.length).cpu().numpy()
            )
        return turn_rewards

    # ----------------------------------------------------------- experience

    def make_experience(self, records: Sequence[EpisodeRecord]) -> list[EpisodeExperience]:
        experiences: list[EpisodeExperience] = []
        dropped = 0
        for record in records:
            experience = build_experience(
                record,
                self.tokenizer,
                reward_config=self.reward_config,
                credit=self.opd_config.credit,
                mode=self.sequence_mode,
                max_history_turns=self.opd_config.rollout.max_history_turns,
                max_seq_len=self.opd_config.model.max_seq_len,
            )
            if experience is None:
                dropped += 1
                continue

            teacher_rewards = self.score_with_teacher(experience, record)
            if not self.opd_config.use_environment_reward:
                for turn in experience.turns:
                    turn.reward = 0.0
            if self.opd_config.opd_loss == "reward":
                for turn, bonus in zip(experience.turns, teacher_rewards, strict=False):
                    turn.reward += bonus

            values = self.turn_values(experience)
            attach_credit(experience, values, self.opd_config.credit)
            experiences.append(experience)

        if self.opd_config.credit.whiten_advantages:
            whiten_batch(experiences)
        if is_main_process():
            print("  experience:", summarize(experiences, dropped_misaligned=dropped).describe())
        return experiences

    # -------------------------------------------------------------- update

    def update_with_mix(self, experiences: Sequence[EpisodeExperience], iteration: int) -> dict[str, float]:
        import torch

        opd_weight, env_weight = self.schedule.weights(iteration)
        mix = MixedLossConfig(
            opd_weight=opd_weight,
            env_weight=env_weight if self.opd_config.use_environment_reward else 0.0,
            opd_loss=self.opd_config.opd_loss,
        )
        aggregate: dict[str, float] = {}
        batches = 0

        for experience in experiences:
            for sequence in experience.sequences:
                input_ids = torch.tensor([sequence.input_ids], device=self.device)
                action_mask = shift_mask(torch.tensor([sequence.action_mask], dtype=torch.float32, device=self.device))
                if action_mask.sum() == 0:
                    continue
                advantages = shift_mask(torch.tensor([sequence.advantages], dtype=torch.float32, device=self.device))
                returns = shift_mask(torch.tensor([sequence.returns], dtype=torch.float32, device=self.device))

                with torch.no_grad():
                    old_logprobs = gather_logprobs(self.actor(input_ids=input_ids).logits, input_ids)
                    ref_logprobs = (
                        gather_logprobs(self.reference(input_ids=input_ids).logits, input_ids)
                        if self.reference is not None
                        else None
                    )

                outputs = self.actor(input_ids=input_ids, output_hidden_states=self.value_head is not None)
                logprobs = gather_logprobs(outputs.logits, input_ids)
                values = self.value_head(outputs.hidden_states[-1]) if self.value_head is not None else None

                metrics: dict[str, float] = {}
                ppo_term = torch.zeros((), device=self.device)
                if mix.env_weight:
                    ppo_term, ppo_metrics = combined_loss(
                        logprobs,
                        old_logprobs,
                        advantages,
                        action_mask,
                        self.loss_config,
                        values=values,
                        returns=returns if values is not None else None,
                        ref_logprobs=ref_logprobs,
                    )
                    metrics.update(ppo_metrics)

                opd_term = torch.zeros((), device=self.device)
                if mix.uses_direct_kl and sequence.has_teacher_signal:
                    teacher_logprobs = shift_mask(
                        torch.tensor([sequence.teacher_logprobs], dtype=torch.float32, device=self.device)
                    )
                    weights = shift_mask(
                        torch.tensor([sequence.teacher_weights], dtype=torch.float32, device=self.device)
                    )
                    opd_term, opd_metrics = opd_loss(logprobs, teacher_logprobs, action_mask, weights)
                    metrics.update(opd_metrics)

                loss, mix_metrics = mixed_loss(ppo_term, opd_term, mix)
                metrics.update(mix_metrics)
                if float(loss.item()) == 0.0 and not loss.requires_grad:
                    continue
                self.backward(loss)
                batches += 1
                for key, value in metrics.items():
                    aggregate[key] = aggregate.get(key, 0.0) + value

        averaged = {key: value / max(batches, 1) for key, value in aggregate.items()}
        averaged["mix/opd_weight"] = opd_weight
        averaged["mix/env_weight"] = mix.env_weight
        return averaged

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        save_config(self.opd_config, self.output_dir)
        for iteration in range(self.opd_config.iterations):
            self.gate_stats = {}
            records = self.collect(iteration)
            rollout_summary = quick_summary(records)
            experiences = self.make_experience(records)
            metrics = self.update_with_mix(experiences, iteration)

            scored = sum(1 for experience in experiences for turn in experience.turns if turn.teacher_weight > 0)
            total_turns = sum(len(experience.turns) for experience in experiences)
            payload = {
                "iteration": iteration,
                "rollout/success_rate": rollout_summary.get("success_rate", 0.0),
                "rollout/recovered_rate": rollout_summary.get("recovered_rate", 0.0),
                "rollout/invalid_actions": rollout_summary.get("invalid_actions", 0),
                "rollout/mean_turns": rollout_summary.get("mean_turns", 0.0),
                "teacher/scored_turns": scored,
                "teacher/scored_fraction": round(scored / max(total_turns, 1), 4),
                "teacher/gate_reasons": dict(sorted(self.gate_stats.items())),
                **metrics,
            }
            if self.opd_config.eval_every_iters and iteration % self.opd_config.eval_every_iters == 0:
                payload.update(self.evaluate(iteration))
            self.logger.log(iteration, payload)

            self.sync_rollout_weights()
            if self.opd_config.save_every_iters and (iteration + 1) % self.opd_config.save_every_iters == 0:
                save_checkpoint(self.engine, self.tokenizer, self.output_dir, f"iter_{iteration + 1}")

        save_checkpoint(self.engine, self.tokenizer, self.output_dir, "final")
        print(f"checkpoint -> {self.output_dir / 'final'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_args(parser, "opd")
    parser.add_argument("--local_rank", type=int, default=-1)
    args = parser.parse_args()

    config: OPDTrainConfig = load_config("opd", args.config, args.overrides)
    if is_main_process():
        print(
            json.dumps(
                {
                    "teacher": config.teacher.path,
                    "opd_loss": config.opd_loss,
                    "use_environment_reward": config.use_environment_reward,
                    "mix": config.mix.__dict__,
                    "reward_preset": config.reward.preset,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    runner = OPDRunner(config)
    runner.setup()
    runner.run()


if __name__ == "__main__":
    main()
