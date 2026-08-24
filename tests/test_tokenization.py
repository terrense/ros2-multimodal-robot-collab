"""Assistant spans decide the SFT loss mask and where PPO advantages land.

The tests use a deterministic fake chat template instead of downloading a real
tokenizer, so span logic is verified offline and in CI.
"""

from __future__ import annotations

import pytest

from training.tokenization import tokenize_episode, tokenize_turn


class FakeTokenizer:
    """Character-level stand-in with a Qwen-style, prefix-stable chat template."""

    chat_template = "{% for message in messages %}...{% endfor %}"

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False, **kwargs):
        text = "".join(f"<{message['role']}>{message['content']}</>" for message in messages)
        if add_generation_prompt:
            text += "<assistant>"
        return [ord(char) for char in text] if tokenize else text


class UnstableTokenizer(FakeTokenizer):
    """Rewrites earlier turns when new ones arrive; spans cannot be trusted."""

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False, **kwargs):
        ids = super().apply_chat_template(messages, tokenize, add_generation_prompt, **kwargs)
        return [len(messages)] + list(ids)


MESSAGES = [
    {"role": "system", "content": "rules"},
    {"role": "user", "content": "obs-1"},
    {"role": "assistant", "content": "act-1"},
    {"role": "user", "content": "obs-2"},
    {"role": "assistant", "content": "act-2"},
]


def _decode(ids) -> str:
    return "".join(chr(value) for value in ids)


def test_every_assistant_turn_gets_a_span() -> None:
    tokens = tokenize_episode(FakeTokenizer(), MESSAGES)
    assert len(tokens.spans) == 2
    assert tokens.turn_indices == [2, 4]


def test_spans_cover_the_action_text_and_nothing_before_it() -> None:
    tokens = tokenize_episode(FakeTokenizer(), MESSAGES)
    first, second = tokens.spans
    assert "act-1" in _decode(tokens.input_ids[first[0] : first[1]])
    assert "act-2" in _decode(tokens.input_ids[second[0] : second[1]])
    assert "obs-1" not in _decode(tokens.input_ids[first[0] : first[1]])
    assert "obs-2" not in _decode(tokens.input_ids[first[0] : first[1]])


def test_labels_mask_out_observations() -> None:
    tokens = tokenize_episode(FakeTokenizer(), MESSAGES)
    labels = tokens.labels()
    assert len(labels) == tokens.length
    supervised = _decode(value for value in labels if value != -100)
    assert "act-1" in supervised and "act-2" in supervised
    assert "obs-1" not in supervised and "rules" not in supervised


def test_action_mask_matches_the_spans() -> None:
    tokens = tokenize_episode(FakeTokenizer(), MESSAGES)
    mask = tokens.action_mask()
    assert sum(mask) == sum(end - start for start, end in tokens.spans)


def test_unstable_template_is_rejected_instead_of_shifting_the_mask() -> None:
    with pytest.raises(ValueError):
        tokenize_episode(UnstableTokenizer(), MESSAGES)


def test_non_strict_mode_skips_unverifiable_turns() -> None:
    tokens = tokenize_episode(UnstableTokenizer(), MESSAGES, strict=False)
    assert tokens.spans == []


def test_turn_mode_splits_prompt_from_completion() -> None:
    prompt_ids, completion_ids = tokenize_turn(FakeTokenizer(), MESSAGES[:2], "act-1")
    assert _decode(prompt_ids).endswith("<assistant>")
    assert "act-1" in _decode(completion_ids)
    assert "obs-1" not in _decode(completion_ids)
