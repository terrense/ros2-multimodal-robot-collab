#!/usr/bin/env python3
"""Step-level PPO against the closed-loop environment.

    deepspeed --num_gpus 4 -m training.train_ppo --config training/configs/ppo_progress.yaml

One iteration:

    1. roll out N episodes with the current policy (sampled, not greedy)
    2. score every turn with the composite step reward
    3. tokenize each episode and locate the assistant span of every turn
    4. forward pass: old log-probs, reference log-probs, per-turn values
    5. GAE over turns -> per-turn advantage -> the token span that earned it
    6. clipped PPO update on action tokens only

Step 5 is the point of the whole file. `--set reward.preset=terminal_only`
reproduces outcome-only PPO, and `--set credit.terminal_broadcast=true`
reproduces spreading one final scalar over every generated token, so the two
things this project claims are different can be run against each other with the
same code and the same rollouts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .config import PPOConfig, add_config_args, load_config, save_config
from .data.scenario_bank import ScenarioSpec, load_specs, sample_scenarios
from .envs import EpisodeRecord
from .policies.base import GenerationConfig
from .rewards import PRESETS, RewardConfig
from .rollout import quick_summary, rollout
from .trainers.common import (
    MetricLogger,
    hidden_size_of,
    initialize_engine,
    is_main_process,
    load_causal_lm,
    load_tokenizer,
    save_checkpoint,
)
from .trainers.experience import EpisodeExperience, attach_credit, build_experience, summarize, whiten_batch
from .trainers.ppo import (
    PPOLossConfig,
    ValueHead,
    combined_loss,
    entropy_from_logits,
    gather_logprobs,
    shift_mask,
)

BENCHMARK = Path("benchmarks/fault_injection_v1.jsonl")


def reward_config_from(config: Any) -> RewardConfig:
    base = PRESETS[config.reward.preset]
    return base.with_overrides(**config.reward.overrides) if config.reward.overrides else base


class PPORunner:
    """Owns the models, the rollout policy and the update loop."""

    def __init__(self, config: PPOConfig) -> None:
        self.config = config
        self.output_dir = Path(config.run.output_dir)
        self.reward_config = reward_config_from(config)
        self.loss_config = PPOLossConfig(
            clip_ratio=config.clip_ratio,
            value_clip=config.value_clip,
            vf_coef=config.vf_coef,
            entropy_coef=config.entropy_coef,
            kl_coef=config.kl_coef,
        )
        self.logger = MetricLogger(
            self.output_dir / "metrics.jsonl",
            keys=("iteration", "rollout/success_rate", "rollout/mean_reward", "policy/loss", "policy/approx_kl"),
        )
        self.sequence_mode = "turn" if config.rollout.max_history_turns is not None else "episode"

    # ------------------------------------------------------------------ setup

    def setup(self) -> None:
        import torch

        self.tokenizer = load_tokenizer(self.config.model)
        self.actor = load_causal_lm(self.config.model, trainable=True)
        hidden = hidden_size_of(self.actor)

        self.value_head = None
        self.critic = None
        if self.config.critic_mode == "shared":
            self.value_head = ValueHead(hidden, dtype=next(self.actor.parameters()).dtype)
            self.value_head.module.to(next(self.actor.parameters()).device)
        elif self.config.critic_mode == "separate":
            self.critic = load_causal_lm(self.config.model, self.config.critic_path, trainable=True)
            self.critic_head = ValueHead(hidden_size_of(self.critic))

        self.reference = None
        if self.config.kl_coef:
            self.reference = load_causal_lm(self.config.model, self.config.reference_path, trainable=False)
            if torch.cuda.is_available():
                self.reference.to(torch.cuda.current_device())

        self.engine, self.optimizer = initialize_engine(
            self.actor, self.config.optim, self.config.run, hidden
        )
        self.device = next(self.actor.parameters()).device
        self.rollout_policy = self._build_rollout_policy()

    def _build_rollout_policy(self) -> Any:
        rollout_config = self.config.rollout
        generation = GenerationConfig(
            max_new_tokens=rollout_config.max_new_tokens,
            temperature=rollout_config.temperature,
            top_p=rollout_config.top_p,
        )
        if rollout_config.backend == "vllm":
            from .policies.llm_policy import VLLMPolicy

            return VLLMPolicy(
                self.config.model.path,
                generation,
                tensor_parallel_size=rollout_config.tensor_parallel_size,
                gpu_memory_utilization=rollout_config.gpu_memory_utilization,
                max_model_len=self.config.model.max_seq_len,
                repair_attempts=rollout_config.repair_attempts,
                tokenizer=self.tokenizer,
            )
        from .policies.llm_policy import HFPolicy

        return HFPolicy(
            self.config.model.path,
            generation,
            repair_attempts=rollout_config.repair_attempts,
            model=self.actor,
            tokenizer=self.tokenizer,
        )

    def sync_rollout_weights(self) -> None:
        """Push updated actor weights into the rollout engine."""
        if self.config.rollout.backend != "vllm":
            return  # the HF policy already holds a reference to the live actor
        state = {name: parameter.data for name, parameter in self.actor.named_parameters()}
        self.rollout_policy.sync_weights_from(state)

    # -------------------------------------------------------------- rollout

    def collect(self, iteration: int) -> list[EpisodeRecord]:
        rollout_config = self.config.rollout
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
            source=f"ppo_iter_{iteration}",
            verbose=False,
        )

    # ----------------------------------------------------------- experience

    def make_experience(self, records: Sequence[EpisodeRecord]) -> list[EpisodeExperience]:
        experiences: list[EpisodeExperience] = []
        dropped = 0
        for record in records:
            experience = build_experience(
                record,
                self.tokenizer,
                reward_config=self.reward_config,
                credit=self.config.credit,
                mode=self.sequence_mode,
                max_history_turns=self.config.rollout.max_history_turns,
                max_seq_len=self.config.model.max_seq_len,
            )
            if experience is None:
                dropped += 1
                continue
            experiences.append(experience)

        for experience in experiences:
            values = self.turn_values(experience)
            attach_credit(experience, values, self.config.credit)
        if self.config.credit.whiten_advantages:
            whiten_batch(experiences)

        stats = summarize(experiences, dropped_misaligned=dropped)
        if is_main_process():
            print("  experience:", stats.describe())
        return experiences

    def turn_values(self, experience: EpisodeExperience) -> list[float] | None:
        """Critic estimate for each turn, read at the last token of its span."""
        if self.config.critic_mode == "none":
            return None
        import torch

        values: list[float] = []
        for sequence in experience.sequences:
            input_ids = torch.tensor([sequence.input_ids], device=self.device)
            with torch.no_grad():
                if self.critic is not None:
                    hidden = self.critic(input_ids=input_ids, output_hidden_states=True).hidden_states[-1]
                    per_token = self.critic_head(hidden)
                else:
                    hidden = self.actor(input_ids=input_ids, output_hidden_states=True).hidden_states[-1]
                    per_token = self.value_head(hidden)
            for start, end in sequence.spans:
                position = min(max(end - 2, 0), per_token.shape[1] - 1)
                values.append(float(per_token[0, position].item()))
        return values

    # -------------------------------------------------------------- update

    def update(self, experiences: Sequence[EpisodeExperience]) -> dict[str, float]:
        import torch

        sequences = [(experience, sequence) for experience in experiences for sequence in experience.sequences]
        if not sequences:
            return {}

        aggregate: dict[str, float] = {}
        batches = 0
        for _ in range(self.config.ppo_epochs):
            for _, sequence in sequences:
                input_ids = torch.tensor([sequence.input_ids], device=self.device)
                action_mask = shift_mask(torch.tensor([sequence.action_mask], dtype=torch.float32, device=self.device))
                advantages = shift_mask(torch.tensor([sequence.advantages], dtype=torch.float32, device=self.device))
                returns = shift_mask(torch.tensor([sequence.returns], dtype=torch.float32, device=self.device))
                if action_mask.sum() == 0:
                    continue

                with torch.no_grad():
                    old_logprobs = gather_logprobs(self.actor(input_ids=input_ids).logits, input_ids)
                    ref_logprobs = None
                    if self.reference is not None:
                        ref_logprobs = gather_logprobs(self.reference(input_ids=input_ids).logits, input_ids)

                outputs = self.actor(input_ids=input_ids, output_hidden_states=self.value_head is not None)
                logprobs = gather_logprobs(outputs.logits, input_ids)
                entropy = entropy_from_logits(outputs.logits) if self.config.entropy_coef else None
                values = self.value_head(outputs.hidden_states[-1]) if self.value_head is not None else None

                loss, metrics = combined_loss(
                    logprobs,
                    old_logprobs,
                    advantages,
                    action_mask,
                    self.loss_config,
                    values=values,
                    returns=returns if values is not None else None,
                    ref_logprobs=ref_logprobs,
                    entropy=entropy,
                )
                self.backward(loss)
                batches += 1
                for key, value in metrics.items():
                    aggregate[key] = aggregate.get(key, 0.0) + value

        return {key: value / max(batches, 1) for key, value in aggregate.items()}

    def backward(self, loss: Any) -> None:
        if hasattr(self.engine, "backward"):
            self.engine.backward(loss)
            self.engine.step()
            return
        import torch

        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.actor.parameters(), self.config.optim.max_grad_norm)
        self.optimizer.step()
        self.optimizer.zero_grad(set_to_none=True)

    # ------------------------------------------------------------ evaluation

    def evaluate(self, iteration: int) -> dict[str, Any]:
        if not BENCHMARK.exists():
            return {}
        specs: list[ScenarioSpec] = load_specs(BENCHMARK)[: self.config.eval_episodes]
        greedy = GenerationConfig(max_new_tokens=self.config.rollout.max_new_tokens, temperature=0.0)
        previous = self.rollout_policy.generation
        self.rollout_policy.generation = greedy
        try:
            records = rollout(self.rollout_policy, specs, concurrency=self.config.rollout.concurrency, verbose=False)
        finally:
            self.rollout_policy.generation = previous
        summary = quick_summary(records)
        return {f"eval/{key}": value for key, value in summary.items() if isinstance(value, (int, float))}

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        save_config(self.config, self.output_dir)
        for iteration in range(self.config.iterations):
            records = self.collect(iteration)
            rollout_summary = quick_summary(records)
            experiences = self.make_experience(records)
            metrics = self.update(experiences)

            payload = {
                "iteration": iteration,
                "rollout/success_rate": rollout_summary.get("success_rate", 0.0),
                "rollout/recovered_rate": rollout_summary.get("recovered_rate", 0.0),
                "rollout/invalid_actions": rollout_summary.get("invalid_actions", 0),
                "rollout/safety_violations": rollout_summary.get("safety_violations", 0),
                "rollout/mean_turns": rollout_summary.get("mean_turns", 0.0),
                "rollout/mean_reward": round(
                    sum(experience.total_reward for experience in experiences) / max(len(experiences), 1), 4
                ),
                **metrics,
            }
            if self.config.eval_every_iters and iteration % self.config.eval_every_iters == 0:
                payload.update(self.evaluate(iteration))
            self.logger.log(iteration, payload)

            self.sync_rollout_weights()
            if self.config.save_every_iters and (iteration + 1) % self.config.save_every_iters == 0:
                save_checkpoint(self.engine, self.tokenizer, self.output_dir, f"iter_{iteration + 1}")

        save_checkpoint(self.engine, self.tokenizer, self.output_dir, "final")
        print(f"checkpoint -> {self.output_dir / 'final'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_args(parser, "ppo")
    parser.add_argument("--local_rank", type=int, default=-1)
    args = parser.parse_args()

    config: PPOConfig = load_config("ppo", args.config, args.overrides)
    if is_main_process():
        print(json.dumps({"reward_preset": config.reward.preset, "credit": config.credit.__dict__}, indent=2))
    runner = PPORunner(config)
    runner.setup()
    runner.run()


if __name__ == "__main__":
    main()
