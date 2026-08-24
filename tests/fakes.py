"""Deterministic stand-ins so tokenization-dependent code is testable offline."""

from __future__ import annotations


class FakeTokenizer:
    """Character-level tokenizer with a prefix-stable, Qwen-style chat template."""

    chat_template = "{% for message in messages %}...{% endfor %}"
    pad_token_id = 0
    eos_token = "</>"

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False, **kwargs):
        text = "".join(f"<{message['role']}>{message['content']}</>" for message in messages)
        if add_generation_prompt:
            text += "<assistant>"
        return [ord(char) for char in text] if tokenize else text

    def decode(self, ids, **kwargs) -> str:
        return "".join(chr(value) for value in ids)


class UnstableTokenizer(FakeTokenizer):
    """Rewrites earlier turns when new ones arrive; spans cannot be trusted."""

    def apply_chat_template(self, messages, tokenize=True, add_generation_prompt=False, **kwargs):
        ids = super().apply_chat_template(messages, tokenize, add_generation_prompt, **kwargs)
        return [len(messages)] + list(ids)


def decode(ids) -> str:
    return "".join(chr(value) for value in ids)
