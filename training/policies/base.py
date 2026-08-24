"""Policy interface shared by the scripted baselines and the LLM backends.

Everything that drives the environment implements `act_batch`, because the
rollout loop steps many episodes in lockstep: one batched generate call per
turn instead of one call per turn per episode. A single-episode `act` is the
degenerate case, not the other way round.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

Messages = list[dict[str, str]]


@dataclass
class GenerationConfig:
    """Decoding settings. Greedy for evaluation, sampled for PPO/OPD rollouts."""

    max_new_tokens: int = 256
    temperature: float = 0.0
    top_p: float = 1.0
    top_k: int = -1
    repetition_penalty: float = 1.0
    seed: int | None = None
    stop: list[str] = field(default_factory=list)
    #: Qwen3 chat template flag. Actions are plain JSON, so thinking stays off.
    enable_thinking: bool = False

    @property
    def greedy(self) -> bool:
        return self.temperature <= 0.0


class Policy(Protocol):
    """Maps chat histories to assistant messages."""

    name: str

    def act_batch(self, batch: Sequence[Messages]) -> list[str]: ...


class BasePolicy:
    """Convenience base: implement `act_batch`, get `act` and `__call__` for free."""

    name: str = "policy"
    #: True when the policy carries per-episode state. The rollout driver then
    #: gives each episode its own instance and runs them sequentially instead of
    #: batching several episodes through one shared object.
    stateful: bool = False

    def act_batch(self, batch: Sequence[Messages]) -> list[str]:  # pragma: no cover - abstract
        raise NotImplementedError

    def act(self, messages: Messages) -> str:
        return self.act_batch([messages])[0]

    def __call__(self, messages: Messages) -> str:
        return self.act(messages)

    def reset(self) -> None:
        """Called at the start of each episode by policies that keep per-episode state."""


__all__ = ["BasePolicy", "GenerationConfig", "Messages", "Policy"]
