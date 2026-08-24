#!/usr/bin/env python3
"""DPO on environment-verified preference pairs.

    deepspeed --num_gpus 4 -m training.train_dpo --config training/configs/dpo_qwen3_8b.yaml

SFT cannot say which of two successful trajectories is better. Both delivered the
tool; one took eight steps and one took fourteen after three blind retries. That
ordering is what DPO learns here, over pairs whose labels were produced by
executing both branches in the simulator (see `training/data/build_dpo.py`), so
the preference direction agrees with the reward PPO later optimizes.

The pairs are conversational and may be multi-turn, so the loss is implemented
directly rather than through a single-response DPO helper: `prompt` is masked out
and the log-probabilities are summed over every response token in the branch.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import DPOConfig, add_config_args, load_config, save_config
from .datasets import DPOCollator, DPODataset, IGNORE_INDEX, read_jsonl


def masked_logprob_sum(logits: Any, labels: Any) -> Any:
    """Sum of log p(token) over the response tokens of each row."""
    import torch

    logits = logits[:, :-1, :].float()
    targets = labels[:, 1:]
    mask = targets != IGNORE_INDEX
    safe_targets = targets.masked_fill(~mask, 0)
    log_probs = torch.log_softmax(logits, dim=-1)
    token_logprobs = torch.gather(log_probs, dim=-1, index=safe_targets.unsqueeze(-1)).squeeze(-1)
    return (token_logprobs * mask).sum(dim=-1)


def dpo_loss(
    policy_chosen: Any,
    policy_rejected: Any,
    reference_chosen: Any,
    reference_rejected: Any,
    beta: float,
    loss_type: str = "sigmoid",
    label_smoothing: float = 0.0,
) -> tuple[Any, dict[str, float]]:
    import torch
    import torch.nn.functional as functional

    policy_margin = policy_chosen - policy_rejected
    reference_margin = reference_chosen - reference_rejected
    logits = policy_margin - reference_margin

    if loss_type == "ipo":
        losses = (logits - 1 / (2 * beta)) ** 2
    elif loss_type == "hinge":
        losses = torch.relu(1 - beta * logits)
    else:  # sigmoid (standard DPO)
        losses = (
            -functional.logsigmoid(beta * logits) * (1 - label_smoothing)
            - functional.logsigmoid(-beta * logits) * label_smoothing
        )

    chosen_reward = beta * (policy_chosen - reference_chosen).detach()
    rejected_reward = beta * (policy_rejected - reference_rejected).detach()
    metrics = {
        "rewards/chosen": chosen_reward.mean().item(),
        "rewards/rejected": rejected_reward.mean().item(),
        "rewards/margin": (chosen_reward - rejected_reward).mean().item(),
        "rewards/accuracy": (chosen_reward > rejected_reward).float().mean().item(),
        "logps/chosen": policy_chosen.mean().item(),
        "logps/rejected": policy_rejected.mean().item(),
    }
    return losses.mean(), metrics


def build_trainer_class():
    """Defined lazily so importing this module does not require transformers."""
    from transformers import Trainer

    class DPOTrainer(Trainer):
        def __init__(self, *args: Any, reference_model: Any, beta: float, loss_type: str, label_smoothing: float, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.reference_model = reference_model
            self.beta = beta
            self.loss_type = loss_type
            self.label_smoothing = label_smoothing

        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            import torch

            input_ids = inputs["input_ids"]
            attention_mask = inputs["attention_mask"]
            labels = inputs["labels"]

            policy_logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
            policy_logps = masked_logprob_sum(policy_logits, labels)
            with torch.no_grad():
                reference_logits = self.reference_model(input_ids=input_ids, attention_mask=attention_mask).logits
                reference_logps = masked_logprob_sum(reference_logits, labels)

            # Rows alternate chosen, rejected (see DPOCollator).
            loss, metrics = dpo_loss(
                policy_logps[0::2],
                policy_logps[1::2],
                reference_logps[0::2],
                reference_logps[1::2],
                self.beta,
                self.loss_type,
                self.label_smoothing,
            )
            self.log(metrics)
            return (loss, policy_logits) if return_outputs else loss

    return DPOTrainer


def load_causal_model(path: str, config: DPOConfig, trainable: bool):
    import torch
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(
        path,
        torch_dtype=getattr(torch, config.model.dtype, torch.bfloat16),
        attn_implementation=config.model.attn_implementation,
        trust_remote_code=config.model.trust_remote_code,
    )
    if trainable:
        if config.model.gradient_checkpointing:
            model.gradient_checkpointing_enable()
            model.config.use_cache = False
    else:
        model.eval()
        for parameter in model.parameters():
            parameter.requires_grad_(False)
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_args(parser, "dpo")
    parser.add_argument("--local_rank", type=int, default=-1)
    args = parser.parse_args()

    config: DPOConfig = load_config("dpo", args.config, args.overrides)
    output_dir = Path(config.run.output_dir)
    save_config(config, output_dir)

    import torch
    from transformers import AutoTokenizer, TrainingArguments

    tokenizer = AutoTokenizer.from_pretrained(config.model.path, trust_remote_code=config.model.trust_remote_code)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token

    train_rows = read_jsonl(config.data.train_path, config.data.max_samples)
    train_dataset = DPODataset(train_rows, tokenizer, config.model.max_seq_len, config.max_prompt_len)
    print(f"train: {train_dataset.report.describe()}")
    eval_dataset = None
    if config.data.val_path and Path(config.data.val_path).exists():
        eval_dataset = DPODataset(read_jsonl(config.data.val_path), tokenizer, config.model.max_seq_len, config.max_prompt_len)
        print(f"val:   {eval_dataset.report.describe()}")
    if not len(train_dataset):
        raise SystemExit("no usable preference pairs; check max_seq_len / max_prompt_len")

    policy = load_causal_model(config.model.path, config, trainable=True)
    # The reference is the SFT checkpoint this run starts from. It stays frozen and
    # unsharded: one bf16 copy per GPU is affordable next to a ZeRO-3 policy.
    reference = load_causal_model(config.reference_path or config.model.path, config, trainable=False)
    if torch.cuda.is_available():
        reference.to(torch.cuda.current_device())

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=config.run.epochs,
        per_device_train_batch_size=config.run.micro_batch_size,
        per_device_eval_batch_size=config.run.micro_batch_size,
        gradient_accumulation_steps=config.run.gradient_accumulation,
        learning_rate=config.optim.lr,
        weight_decay=config.optim.weight_decay,
        max_grad_norm=config.optim.max_grad_norm,
        warmup_ratio=config.optim.warmup_ratio,
        lr_scheduler_type=config.optim.scheduler,
        logging_steps=config.run.log_every,
        eval_strategy="steps" if eval_dataset else "no",
        eval_steps=config.run.eval_every,
        save_steps=config.run.save_every,
        save_total_limit=3,
        bf16=config.model.dtype == "bfloat16",
        gradient_checkpointing=config.model.gradient_checkpointing,
        deepspeed=config.run.deepspeed,
        seed=config.run.seed,
        report_to=[config.run.report_to] if config.run.report_to != "none" else [],
        run_name=config.run.run_name or output_dir.name,
        remove_unused_columns=False,
    )

    trainer = build_trainer_class()(
        model=policy,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=DPOCollator(tokenizer.pad_token_id),
        reference_model=reference,
        beta=config.beta,
        loss_type=config.loss_type,
        label_smoothing=config.label_smoothing,
    )

    result = trainer.train()
    trainer.save_model(str(output_dir / "final"))
    tokenizer.save_pretrained(str(output_dir / "final"))
    (output_dir / "train_metrics.json").write_text(
        json.dumps(dict(result.metrics), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"checkpoint -> {output_dir / 'final'}")


if __name__ == "__main__":
    main()
