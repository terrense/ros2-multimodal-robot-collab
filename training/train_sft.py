#!/usr/bin/env python3
"""Full-parameter SFT on expert / repaired trajectories.

    deepspeed --num_gpus 4 -m training.train_sft --config training/configs/sft_qwen3_8b.yaml

What SFT is for here: the model does not yet know how to talk to this
environment -- the skill schema, the argument shapes, what a ROS2 error code
means, which recovery skills are legal after a given failure. SFT installs that
behaviour prior. It cannot express that one successful trajectory is better than
another; that is DPO's job, and closing the remaining gap on states the data
never contained is PPO's.

Loss is computed on assistant spans only. Observations are inputs, never targets:
training the model to reproduce observation JSON would waste capacity on text the
environment always supplies.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .config import SFTConfig, add_config_args, load_config, save_config
from .datasets import IGNORE_INDEX, PaddedCollator, SFTDataset, read_jsonl


def build_tokenizer(config: SFTConfig):
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(config.model.path, trust_remote_code=config.model.trust_remote_code)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def build_model(config: SFTConfig):
    import torch
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(
        config.model.path,
        torch_dtype=getattr(torch, config.model.dtype, torch.bfloat16),
        attn_implementation=config.model.attn_implementation,
        trust_remote_code=config.model.trust_remote_code,
    )
    if config.model.gradient_checkpointing:
        model.gradient_checkpointing_enable()
        model.config.use_cache = False
    if config.model.use_lora:
        from peft import LoraConfig, get_peft_model

        model = get_peft_model(
            model,
            LoraConfig(
                r=config.model.lora_rank,
                lora_alpha=config.model.lora_alpha,
                lora_dropout=config.model.lora_dropout,
                target_modules="all-linear",
                task_type="CAUSAL_LM",
            ),
        )
    return model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_config_args(parser, "sft")
    parser.add_argument("--local_rank", type=int, default=-1)  # injected by the deepspeed launcher
    args = parser.parse_args()

    config: SFTConfig = load_config("sft", args.config, args.overrides)
    output_dir = Path(config.run.output_dir)
    save_config(config, output_dir)

    from transformers import Trainer, TrainingArguments

    tokenizer = build_tokenizer(config)

    train_rows = read_jsonl(config.data.train_path, config.data.max_samples)
    train_dataset = SFTDataset(train_rows, tokenizer, config.model.max_seq_len, config.mask_observations)
    print(f"train: {train_dataset.report.describe()}")

    eval_dataset = None
    if config.data.val_path and Path(config.data.val_path).exists():
        eval_rows = read_jsonl(config.data.val_path)
        eval_dataset = SFTDataset(eval_rows, tokenizer, config.model.max_seq_len, config.mask_observations)
        print(f"val:   {eval_dataset.report.describe()}")

    if not len(train_dataset):
        raise SystemExit("no usable training samples; check max_seq_len and the corpus")

    supervised = sum(
        sum(1 for label in example["labels"] if label != IGNORE_INDEX) for example in train_dataset.examples
    )
    total = sum(len(example["input_ids"]) for example in train_dataset.examples)
    print(f"supervised tokens: {supervised}/{total} ({supervised / max(total, 1):.1%} of the corpus)")

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=config.run.epochs,
        per_device_train_batch_size=config.run.micro_batch_size,
        per_device_eval_batch_size=config.run.micro_batch_size,
        gradient_accumulation_steps=config.run.gradient_accumulation,
        learning_rate=config.optim.lr,
        weight_decay=config.optim.weight_decay,
        adam_beta1=config.optim.beta1,
        adam_beta2=config.optim.beta2,
        adam_epsilon=config.optim.eps,
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

    trainer = Trainer(
        model=build_model(config),
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=PaddedCollator(tokenizer.pad_token_id),
    )

    result = trainer.train()
    trainer.save_model(str(output_dir / "final"))
    tokenizer.save_pretrained(str(output_dir / "final"))

    metrics = dict(result.metrics)
    if eval_dataset:
        evaluation = trainer.evaluate()
        metrics.update(evaluation)
        loss = evaluation.get("eval_loss")
        if loss is not None:
            metrics["eval_perplexity"] = round(math.exp(min(loss, 20)), 4)
    (output_dir / "train_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(f"checkpoint -> {output_dir / 'final'}")


if __name__ == "__main__":
    main()
