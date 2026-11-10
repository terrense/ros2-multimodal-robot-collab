"""Torch datasets and collators over the JSONL corpora.

Both SFT and DPO tokenize through `training.tokenization`, so the loss mask and
the assistant spans come from the same code the RL stages use. An example whose
spans cannot be verified, or that does not fit in `max_seq_len`, is dropped and
counted -- never silently truncated, because a truncated episode would move the
loss mask onto observation tokens.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .provenance import Provenance, require_trainable_records
from .tokenization import tokenize_episode

IGNORE_INDEX = -100


def read_jsonl(
    path: str | Path,
    limit: int | None = None,
    *,
    expected_provenance: Provenance | None = None,
) -> list[dict[str, Any]]:
    """Read a JSONL corpus, refusing samples that drifted from the run record.

    A sample whose prompt or action catalogue differs from the one the run
    record names is not trainable: it was measured under different rules, and
    training on it would silently mix two distributions. v1 samples carry no
    provenance and are accepted unchanged.
    """
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    if expected_provenance is not None:
        checked = require_trainable_records(rows, expected_provenance)
        if checked:
            print(f"{path}: verified {checked} samples against the run record")
    return rows


@dataclass
class DropReport:
    total: int = 0
    too_long: int = 0
    unmaskable: int = 0

    @property
    def kept(self) -> int:
        return self.total - self.too_long - self.unmaskable

    def describe(self) -> str:
        return (
            f"kept {self.kept}/{self.total} samples "
            f"(dropped {self.too_long} over max_seq_len, {self.unmaskable} with unverifiable spans)"
        )


class SFTDataset:
    """Chat episodes tokenized once, with loss on assistant spans only."""

    def __init__(
        self,
        rows: Sequence[dict[str, Any]],
        tokenizer: Any,
        max_seq_len: int = 8192,
        mask_observations: bool = True,
    ) -> None:
        self.examples: list[dict[str, list[int]]] = []
        self.report = DropReport(total=len(rows))
        for row in rows:
            messages = row["messages"]
            try:
                tokens = tokenize_episode(tokenizer, messages)
            except ValueError:
                self.report.unmaskable += 1
                continue
            if len(tokens.input_ids) > max_seq_len:
                self.report.too_long += 1
                continue
            labels = tokens.labels(IGNORE_INDEX) if mask_observations else list(tokens.input_ids)
            self.examples.append(
                {
                    "input_ids": tokens.input_ids,
                    "labels": labels,
                    "attention_mask": [1] * len(tokens.input_ids),
                }
            )

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> dict[str, list[int]]:
        return self.examples[index]


class DPODataset:
    """Preference pairs: shared prompt, two continuations."""

    def __init__(
        self,
        rows: Sequence[dict[str, Any]],
        tokenizer: Any,
        max_seq_len: int = 8192,
        max_prompt_len: int = 6144,
    ) -> None:
        self.examples: list[dict[str, Any]] = []
        self.report = DropReport(total=len(rows))
        for row in rows:
            prompt = row["prompt"]
            try:
                prompt_ids = tokenize_episode(tokenizer, prompt, strict=False).input_ids
                chosen_ids = self._continuation_ids(tokenizer, prompt, prompt_ids, row["chosen"])
                rejected_ids = self._continuation_ids(tokenizer, prompt, prompt_ids, row["rejected"])
            except ValueError:
                self.report.unmaskable += 1
                continue
            if len(prompt_ids) > max_prompt_len or max(len(chosen_ids), len(rejected_ids)) + len(prompt_ids) > max_seq_len:
                self.report.too_long += 1
                continue
            self.examples.append(
                {
                    "prompt_ids": prompt_ids,
                    "chosen_ids": chosen_ids,
                    "rejected_ids": rejected_ids,
                    "meta": row.get("meta") or {},
                }
            )

    @staticmethod
    def _continuation_ids(
        tokenizer: Any,
        prompt: Sequence[dict[str, str]],
        prompt_ids: Sequence[int],
        continuation: Sequence[dict[str, str]],
    ) -> list[int]:
        """Tokens of a (possibly multi-turn) continuation, as a suffix of the prompt.

        Both branches are tokenized as full episodes and split at the same
        boundary, so `prompt_ids + continuation_ids` reconstructs exactly what the
        model will be scored on.
        """
        tokens = tokenize_episode(tokenizer, [*prompt, *continuation], strict=False)
        boundary = len(prompt_ids)
        if list(tokens.input_ids[:boundary]) != list(prompt_ids):
            raise ValueError("prompt is not a prefix of prompt+continuation")
        return tokens.input_ids[boundary:]

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.examples[index]


@dataclass
class PaddedCollator:
    """Right-pads a batch of variable-length sequences."""

    pad_token_id: int
    label_pad_id: int = IGNORE_INDEX

    def __call__(self, batch: Sequence[dict[str, list[int]]]) -> dict[str, Any]:
        import torch

        width = max(len(item["input_ids"]) for item in batch)
        input_ids, labels, attention = [], [], []
        for item in batch:
            pad = width - len(item["input_ids"])
            input_ids.append(item["input_ids"] + [self.pad_token_id] * pad)
            labels.append(item["labels"] + [self.label_pad_id] * pad)
            attention.append(item["attention_mask"] + [0] * pad)
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
        }


@dataclass
class DPOCollator:
    """Builds the four sequences DPO needs: (prompt+chosen, prompt+rejected) x (ids, labels)."""

    pad_token_id: int
    label_pad_id: int = IGNORE_INDEX

    def __call__(self, batch: Sequence[dict[str, Any]]) -> dict[str, Any]:
        import torch

        rows: list[tuple[list[int], list[int]]] = []
        for item in batch:
            for key in ("chosen_ids", "rejected_ids"):
                ids = item["prompt_ids"] + item[key]
                labels = [self.label_pad_id] * len(item["prompt_ids"]) + item[key]
                rows.append((ids, labels))
        width = max(len(ids) for ids, _ in rows)
        input_ids, labels, attention = [], [], []
        for ids, label in rows:
            pad = width - len(ids)
            input_ids.append(ids + [self.pad_token_id] * pad)
            labels.append(label + [self.label_pad_id] * pad)
            attention.append([1] * len(ids) + [0] * pad)
        # Rows alternate chosen, rejected, chosen, rejected, ...
        return {
            "input_ids": torch.tensor(input_ids, dtype=torch.long),
            "labels": torch.tensor(labels, dtype=torch.long),
            "attention_mask": torch.tensor(attention, dtype=torch.long),
            "pair_size": 2,
        }


__all__ = [
    "DPOCollator",
    "DPODataset",
    "DropReport",
    "IGNORE_INDEX",
    "PaddedCollator",
    "SFTDataset",
    "read_jsonl",
]
