"""Policy backends for the closed loop.

Each backend answers the same question: given one model view, what is the next
single action? Only the source of the answer differs.

- ScriptedPolicy: a fixed list (the baseline).
- StubModelPolicy: a deterministic stand-in that also counts characters, so the
  token budget can be exercised without a network or a tokenizer.
- HostedModelPolicy: a hosted model (DeepSeek, MiniMax) asked for the next
  capability; the key comes from the environment.
- HumanPolicy: a callback, used by a debugging driver or a test.
- PlanMacroPolicy: a validated mission plan replayed one step per decision; a
  failed step ends the macro, exactly like the executor's `break`.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .policy import Decide, Decision, Finish


class ScriptedPolicy:
    name = "scripted"

    def __init__(self, decisions: Iterable[Decision]) -> None:
        self._decisions = list(decisions)
        self._index = 0
        self.receipts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._index = 0
        self.receipts = []

    def decide(self, view: dict[str, Any]) -> Decision:
        del view
        if self._index < len(self._decisions):
            decision = self._decisions[self._index]
            self._index += 1
            return decision
        return Finish(claimed_success=False, reason="script exhausted")

    def consume(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)

    def tokens_used(self) -> int:
        return 0


class StubModelPolicy:
    """Deterministic stand-in for a model backend."""

    name = "stub-model"

    def __init__(self, decisions: Iterable[Decision]) -> None:
        self._inner = ScriptedPolicy(decisions)
        self._chars = 0

    def reset(self) -> None:
        self._inner.reset()
        self._chars = 0

    def decide(self, view: dict[str, Any]) -> Decision:
        self._chars += len(json.dumps(view, ensure_ascii=False, default=str))
        return self._inner.decide(view)

    def consume(self, receipt: dict[str, Any]) -> None:
        self._chars += len(json.dumps(receipt, ensure_ascii=False, default=str))
        self._inner.consume(receipt)

    def tokens_used(self) -> int:
        return self._chars


class HostedModelPolicy:
    """Ask a hosted model which capability to call next.

    The model is asked for one decision at a time, the way the loop works:
    one capability, or stop. Its answer is checked against the capabilities
    the harness actually has before it becomes a decision, so a model that
    invents a capability gets a refusal rather than a robot moving.

    The key comes from the environment. This repository is public.
    """

    name = "hosted-model"

    SYSTEM_PROMPT = """You are the decision source of a ROS2 robot collaboration system.

You are given a view of what the robot has just seen. Answer with exactly one
JSON object:

{"capability": "<one capability name>", "arguments": {}, "reason": "<short>"}

or, when the task is done or cannot be done:

{"finish": true, "claimed_success": false, "reason": "<short>"}

Rules:
- Call exactly one capability. Never call two in one answer.
- Only use capabilities from the list you are given.
- Arguments must match the arguments the capability takes.
- Never call a sensor output skill (a detector that publishes results).
- Do not output markdown, explanations, or anything but the JSON object.
"""

    def __init__(
        self,
        capabilities: Iterable[str],
        *,
        provider: str | None = None,
        system_prompt: str | None = None,
        temperature: float = 0.1,
        timeout_sec: float = 60.0,
    ) -> None:
        from .hosted_model import resolve_provider

        self._capabilities = list(capabilities)
        self._provider = resolve_provider(provider)
        self._system = system_prompt or self.SYSTEM_PROMPT
        self._temperature = temperature
        self._timeout_sec = timeout_sec
        self._tokens = 0
        self.receipts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._tokens = 0
        self.receipts = []

    def decide(self, view: dict[str, Any]) -> Decision:
        from .hosted_model import chat_json

        prompt = json.dumps(
            {
                "capabilities": self._capabilities,
                "view": view,
            },
            ensure_ascii=False,
            default=str,
        )
        answer = chat_json(
            prompt,
            system=self._system,
            provider=self._provider,
            temperature=self._temperature,
            timeout_sec=self._timeout_sec,
        )
        self._tokens += len(json.dumps(answer, ensure_ascii=False, default=str))
        if answer.get("finish"):
            return Finish(
                claimed_success=bool(answer.get("claimed_success", False)),
                reason=str(answer.get("reason", "")),
            )
        capability = str(answer.get("capability", ""))
        if capability not in self._capabilities:
            return Finish(
                claimed_success=False,
                reason=(
                    f"the model asked for {capability!r}, which this robot "
                    "cannot do"
                ),
            )
        arguments = answer.get("arguments") or {}
        if not isinstance(arguments, dict):
            return Finish(
                claimed_success=False,
                reason="the model answered with arguments that are not an object",
            )
        return Decide(
            capability=capability,
            arguments=dict(arguments),
            reason=str(answer.get("reason", "")),
        )

    def consume(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)

    def tokens_used(self) -> int:
        return self._tokens


class HumanPolicy:
    name = "human"

    def __init__(self, responder: Callable[[dict[str, Any]], Decision]) -> None:
        self._responder = responder
        self.receipts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.receipts = []

    def decide(self, view: dict[str, Any]) -> Decision:
        return self._responder(view)

    def consume(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)

    def tokens_used(self) -> int:
        return 0


class PlanMacroPolicy:
    """Replay a validated mission plan, one step per decision."""

    name = "plan-macro"

    def __init__(self, steps: Sequence[tuple[str, dict[str, Any]]]) -> None:
        self._steps = list(steps)
        self._index = 0
        self._failed = False
        self.receipts: list[dict[str, Any]] = []

    def reset(self) -> None:
        self._index = 0
        self._failed = False
        self.receipts = []

    def decide(self, view: dict[str, Any]) -> Decision:
        del view
        if self._failed:
            return Finish(claimed_success=False, reason="a plan step failed")
        if self._index >= len(self._steps):
            return Finish(claimed_success=True, reason="plan complete")
        capability, arguments = self._steps[self._index]
        self._index += 1
        return Decide(capability=capability, arguments=dict(arguments))

    def consume(self, receipt: dict[str, Any]) -> None:
        self.receipts.append(receipt)
        if receipt.get("execution_status") in {"failed", "rejected", "unknown"}:
            self._failed = True

    def tokens_used(self) -> int:
        return 0
