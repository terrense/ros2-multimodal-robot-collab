"""Dataset masking and the DPO objective, checked numerically on CPU."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from tests.fakes import FakeTokenizer, decode  # noqa: E402
from training.datasets import (  # noqa: E402
    IGNORE_INDEX,
    DPOCollator,
    DPODataset,
    PaddedCollator,
    SFTDataset,
)
from training.train_dpo import dpo_loss, masked_logprob_sum  # noqa: E402

SFT_ROWS = [
    {
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "obs-1"},
            {"role": "assistant", "content": "act-1"},
            {"role": "user", "content": "obs-2"},
            {"role": "assistant", "content": "act-2"},
        ],
        "meta": {"episode_id": "ep-1"},
    }
]

DPO_ROWS = [
    {
        "prompt": [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "obs-1"},
        ],
        "chosen": [{"role": "assistant", "content": "good-action"}],
        "rejected": [{"role": "assistant", "content": "bad-action"}],
        "meta": {"rule": "blind_retry"},
    }
]


# ---------------------------------------------------------------- SFT masking


def test_sft_dataset_supervises_only_assistant_tokens() -> None:
    dataset = SFTDataset(SFT_ROWS, FakeTokenizer(), max_seq_len=4096)
    example = dataset[0]
    supervised = decode(label for label in example["labels"] if label != IGNORE_INDEX)
    assert "act-1" in supervised and "act-2" in supervised
    assert "obs-1" not in supervised and "rules" not in supervised
    assert len(example["labels"]) == len(example["input_ids"]) == len(example["attention_mask"])


def test_oversized_samples_are_dropped_not_truncated() -> None:
    dataset = SFTDataset(SFT_ROWS, FakeTokenizer(), max_seq_len=8)
    assert len(dataset) == 0
    assert dataset.report.too_long == 1
    assert "dropped 1 over max_seq_len" in dataset.report.describe()


def test_padded_collator_masks_padding_out_of_the_loss() -> None:
    dataset = SFTDataset(SFT_ROWS * 2, FakeTokenizer(), max_seq_len=4096)
    examples = [dataset[0], {**dataset[1], "input_ids": dataset[1]["input_ids"][:-5], "labels": dataset[1]["labels"][:-5], "attention_mask": dataset[1]["attention_mask"][:-5]}]
    batch = PaddedCollator(pad_token_id=0)(examples)
    assert batch["input_ids"].shape == batch["labels"].shape == batch["attention_mask"].shape
    padded_row = batch["labels"][1]
    assert padded_row[-1].item() == IGNORE_INDEX
    assert batch["attention_mask"][1][-1].item() == 0


# ------------------------------------------------------------------ DPO pairs


def test_dpo_dataset_splits_prompt_from_both_branches() -> None:
    dataset = DPODataset(DPO_ROWS, FakeTokenizer(), max_seq_len=4096, max_prompt_len=4096)
    assert len(dataset) == 1
    example = dataset[0]
    assert "obs-1" in decode(example["prompt_ids"])
    assert "good-action" in decode(example["chosen_ids"])
    assert "bad-action" in decode(example["rejected_ids"])
    assert "obs-1" not in decode(example["chosen_ids"])


def test_dpo_collator_emits_alternating_chosen_rejected_rows() -> None:
    dataset = DPODataset(DPO_ROWS * 2, FakeTokenizer(), max_seq_len=4096, max_prompt_len=4096)
    batch = DPOCollator(pad_token_id=0)([dataset[0], dataset[1]])
    assert batch["input_ids"].shape[0] == 4  # two pairs
    labelled = [decode(row[row != IGNORE_INDEX].tolist()) for row in batch["labels"]]
    assert "good-action" in labelled[0] and "bad-action" in labelled[1]
    assert all("obs-1" not in text for text in labelled)  # prompt is masked in every row


# ------------------------------------------------------------------- DPO loss


def _logprob_setup(chosen_boost: float) -> tuple[torch.Tensor, torch.Tensor]:
    torch.manual_seed(0)
    logits = torch.zeros(2, 4, 8)
    labels = torch.full((2, 4), IGNORE_INDEX)
    labels[:, 1:] = torch.tensor([[3, 4, 5], [3, 4, 5]])
    logits[0, :, 3:6] += chosen_boost  # make the chosen row more likely
    return logits, labels


def test_masked_logprob_sum_ignores_masked_positions() -> None:
    logits = torch.zeros(1, 3, 5)
    labels = torch.tensor([[IGNORE_INDEX, 2, IGNORE_INDEX]])
    total = masked_logprob_sum(logits, labels)
    # Only one supervised token; uniform logits over 5 classes.
    assert total.item() == pytest.approx(torch.log(torch.tensor(1 / 5)).item(), abs=1e-5)


def test_dpo_loss_falls_as_the_chosen_branch_gains_probability() -> None:
    reference = torch.zeros(1)
    losses = []
    for boost in (0.0, 1.0, 3.0):
        logits, labels = _logprob_setup(boost)
        logps = masked_logprob_sum(logits, labels)
        loss, _ = dpo_loss(logps[0:1], logps[1:2], reference, reference, beta=0.1)
        losses.append(loss.item())
    assert losses == sorted(losses, reverse=True)


def test_dpo_metrics_report_accuracy_and_margin() -> None:
    logits, labels = _logprob_setup(3.0)
    logps = masked_logprob_sum(logits, labels)
    zero = torch.zeros(1)
    _, metrics = dpo_loss(logps[0:1], logps[1:2], zero, zero, beta=0.1)
    assert metrics["rewards/accuracy"] == 1.0
    assert metrics["rewards/margin"] > 0


def test_reference_cancels_out_when_both_branches_shift_together() -> None:
    """A uniform shift of the reference must not change the objective."""
    policy_chosen, policy_rejected = torch.tensor([2.0]), torch.tensor([1.0])
    base, _ = dpo_loss(policy_chosen, policy_rejected, torch.tensor([0.0]), torch.tensor([0.0]), beta=0.1)
    shifted, _ = dpo_loss(policy_chosen, policy_rejected, torch.tensor([5.0]), torch.tensor([5.0]), beta=0.1)
    assert base.item() == pytest.approx(shifted.item(), abs=1e-6)


@pytest.mark.parametrize("loss_type", ["sigmoid", "ipo", "hinge"])
def test_all_loss_variants_produce_a_finite_scalar(loss_type: str) -> None:
    logits, labels = _logprob_setup(1.0)
    logps = masked_logprob_sum(logits, labels)
    zero = torch.zeros(1)
    loss, _ = dpo_loss(logps[0:1], logps[1:2], zero, zero, beta=0.1, loss_type=loss_type)
    assert torch.isfinite(loss)


def test_gradient_flows_into_the_policy_logits() -> None:
    logits, labels = _logprob_setup(0.5)
    logits.requires_grad_(True)
    logps = masked_logprob_sum(logits, labels)
    zero = torch.zeros(1)
    loss, _ = dpo_loss(logps[0:1], logps[1:2], zero, zero, beta=0.1)
    loss.backward()
    assert logits.grad is not None and torch.isfinite(logits.grad).all()
    assert logits.grad.abs().sum() > 0
