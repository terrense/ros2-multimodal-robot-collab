"""Chat-template tokenization with exact assistant token spans.

Three stages need to know precisely which tokens the policy generated:

    SFT   loss only on assistant tokens (never on the observation JSON)
    PPO   turn advantages scattered onto the action tokens that earned them
    OPD   teacher/student log-prob difference on the student's sampled action tokens

Spans are recovered by re-applying the chat template to growing message prefixes
and diffing lengths, which keeps working when a template inserts per-turn control
tokens. Every span is verified to be a real prefix-continuation of the full
sequence; a mismatch raises instead of silently shifting the loss mask by a token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass
class EpisodeTokens:
    """One tokenized episode plus the token ranges of each assistant turn."""

    input_ids: list[int]
    spans: list[tuple[int, int]]  # [start, end) per assistant turn, in order
    turn_indices: list[int]  # index into `messages` for each span

    @property
    def length(self) -> int:
        return len(self.input_ids)

    def labels(self, ignore_index: int = -100) -> list[int]:
        """Causal-LM labels with everything except assistant tokens masked out."""
        out = [ignore_index] * len(self.input_ids)
        for start, end in self.spans:
            for position in range(start, end):
                out[position] = self.input_ids[position]
        return out

    def action_mask(self) -> list[int]:
        mask = [0] * len(self.input_ids)
        for start, end in self.spans:
            for position in range(start, end):
                mask[position] = 1
        return mask


def _encode(tokenizer: Any, messages: Sequence[dict[str, str]], add_generation_prompt: bool) -> list[int]:
    ids = tokenizer.apply_chat_template(
        list(messages),
        tokenize=True,
        add_generation_prompt=add_generation_prompt,
        # Qwen3 chat templates gate the <think> block on this flag; training data
        # carries plain JSON actions, so thinking must stay off for both.
        **({"enable_thinking": False} if _template_accepts(tokenizer, "enable_thinking") else {}),
    )
    if hasattr(ids, "tolist"):  # some templates return tensors
        ids = ids.tolist()
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    return list(ids)


def _template_accepts(tokenizer: Any, kwarg: str) -> bool:
    template = getattr(tokenizer, "chat_template", None)
    return bool(template) and kwarg in template


def tokenize_episode(
    tokenizer: Any,
    messages: Sequence[dict[str, str]],
    *,
    strict: bool = True,
) -> EpisodeTokens:
    """Tokenize a full chat episode and locate every assistant span.

    `strict=False` skips a turn whose span cannot be verified instead of raising;
    use it only for exploratory tooling, never for a training run.
    """
    messages = list(messages)
    full_ids = _encode(tokenizer, messages, add_generation_prompt=False)
    spans: list[tuple[int, int]] = []
    turn_indices: list[int] = []

    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        prefix_ids = _encode(tokenizer, messages[:index], add_generation_prompt=True)
        through_ids = _encode(tokenizer, messages[: index + 1], add_generation_prompt=False)
        start, end = len(prefix_ids), len(through_ids)
        if end <= start or end > len(full_ids):
            if strict:
                raise ValueError(f"degenerate assistant span at message {index}: [{start}, {end}) of {len(full_ids)}")
            continue
        if full_ids[:start] != prefix_ids or full_ids[:end] != through_ids:
            if strict:
                raise ValueError(
                    f"chat template is not prefix-stable at message {index}; "
                    "assistant spans cannot be trusted for loss masking"
                )
            continue
        spans.append((start, end))
        turn_indices.append(index)

    return EpisodeTokens(input_ids=full_ids, spans=spans, turn_indices=turn_indices)


def tokenize_turn(
    tokenizer: Any,
    messages: Sequence[dict[str, str]],
    completion: str,
) -> tuple[list[int], list[int]]:
    """`(prompt_ids, completion_ids)` for one turn, used by turn-mode PPO/OPD.

    The completion is tokenized as the continuation of the rendered prompt rather
    than on its own, so no boundary token is lost or duplicated.
    """
    prompt_ids = _encode(tokenizer, messages, add_generation_prompt=True)
    full_ids = _encode(tokenizer, [*messages, {"role": "assistant", "content": completion}], add_generation_prompt=False)
    if full_ids[: len(prompt_ids)] != prompt_ids:
        raise ValueError("chat template is not prefix-stable; cannot split prompt from completion")
    return prompt_ids, full_ids[len(prompt_ids) :]


__all__ = ["EpisodeTokens", "tokenize_episode", "tokenize_turn"]
