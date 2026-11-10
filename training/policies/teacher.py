"""Frozen teacher for on-policy distillation, and the gate that decides when to ask it.

The student rolls out on its own; the teacher never contributes trajectories. On
selected turns the teacher is shown the *same* prompt and scores the tokens the
student actually sampled. The signal is the log-probability difference on those
tokens, so the supervision is defined on the student's own state distribution --
which is the point of on-policy distillation and the thing SFT cannot give.

Calling a 30B teacher on every turn is mostly waste: a successful navigation or a
routine verification carries no learning signal. `TurnGate` spends the teacher
budget on the turns that do -- an error code, repeated no-progress, a safety
event, high student uncertainty, or a turn that already disagreed. The gate is
pure Python and tested; only the scorer needs a GPU.

`MixSchedule` implements the OPD -> PPO handover: dense teacher guidance early,
environment reward late, because the teacher only sees text and cannot know what
Gazebo physics will do.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from ..memory import GIB

# --------------------------------------------------------------------- errors


class TeacherPlacementError(RuntimeError):
    """Raised when the teacher cannot be placed where the config asks for it.

    The teacher is 30B -- 60.8 GB in bf16 -- and the training run already holds
    the student, its gradients and its optimizer state on the same cards. There
    is no placement of one teacher copy per rank that fits, so the options are
    a separate server process or a smaller teacher, and the error says which.
    """

# --------------------------------------------------------------------- gating


@dataclass
class GateConfig:
    on_error: bool = True
    on_safety_event: bool = True
    #: Deliberately lower than the reward's threshold of 3: the gate decides
    #: whether a turn is worth asking the teacher about, and catching trouble one
    #: turn earlier costs teacher budget rather than mislabelling the policy.
    no_progress_threshold: int = 2
    entropy_threshold: float = 1.0  # nats, mean over the sampled action tokens
    disagreement_threshold: float = 0.5  # nats, from the previous scored turn
    random_sample_prob: float = 0.05  # keeps an unbiased slice for calibration
    max_scored_fraction: float = 0.5  # hard budget cap across an episode
    max_scored_turns: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TurnFeatures:
    """What the gate is allowed to look at, all available before the teacher runs."""

    turn_index: int
    error_code: str = "OK"
    result_status: str = "succeeded"
    no_progress_count: int = 0
    safety_events: list[str] = field(default_factory=list)
    student_entropy: float = 0.0
    prev_disagreement: float = 0.0


@dataclass
class GateDecision:
    scored: bool
    reason: str


class TurnGate:
    """Per-episode budgeted gate. One instance per episode."""

    def __init__(self, config: GateConfig | None = None, rng: random.Random | None = None) -> None:
        self.config = config or GateConfig()
        self.rng = rng or random.Random(0)
        self.seen = 0
        self.scored = 0

    def reset(self) -> None:
        self.seen = 0
        self.scored = 0

    def _budget_left(self) -> bool:
        config = self.config
        if config.max_scored_turns is not None and self.scored >= config.max_scored_turns:
            return False
        if config.max_scored_fraction >= 1.0:
            return True
        # Compare against turns seen so far, so a long episode earns more budget.
        return self.scored < max(1, math.ceil((self.seen + 1) * config.max_scored_fraction))

    def decide(self, features: TurnFeatures) -> GateDecision:
        self.seen += 1
        config = self.config
        reason = ""

        if config.on_error and (features.error_code not in ("OK", "", None) or features.result_status in ("failed", "rejected")):
            reason = "error"
        elif config.on_safety_event and features.safety_events:
            reason = "safety_event"
        elif features.no_progress_count >= config.no_progress_threshold:
            reason = "no_progress"
        elif features.student_entropy >= config.entropy_threshold:
            reason = "uncertain"
        elif features.prev_disagreement >= config.disagreement_threshold:
            reason = "prior_disagreement"
        elif self.rng.random() < config.random_sample_prob:
            reason = "random_sample"

        if not reason:
            return GateDecision(False, "routine")
        if not self._budget_left():
            return GateDecision(False, f"budget_exhausted:{reason}")
        self.scored += 1
        return GateDecision(True, reason)


def features_from_turn(turn: Any, student_entropy: float = 0.0, prev_disagreement: float = 0.0) -> TurnFeatures:
    """Build gate features from a `TurnRecord` (or its dict form).

    `error_code` and `no_progress_count` come from the observation the policy was
    looking at, so they describe the *state the decision was made in* -- a
    recovery turn -- rather than how that decision happened to turn out.
    `result_status` is the turn's own outcome, which is also known by the time
    teacher scoring runs and marks an action that failed outright.
    """
    data = turn if isinstance(turn, dict) else turn.to_dict()
    observation = data.get("observation") or {}
    previous = observation.get("last_result") or {}
    result = data.get("result") or {}
    info = data.get("info") or {}
    return TurnFeatures(
        turn_index=int(data.get("turn_index", 0)),
        error_code=str(previous.get("error_code", "OK")),
        result_status=str(result.get("status", "none")),
        no_progress_count=int(observation.get("no_progress_count", info.get("no_progress_count", 0))),
        safety_events=list(observation.get("safety_events") or []),
        student_entropy=student_entropy,
        prev_disagreement=prev_disagreement,
    )


# ---------------------------------------------------------------- weighting


@dataclass
class OPDConfig:
    """How strongly one scored turn is distilled."""

    base_weight: float = 1.0
    uncertainty_scale: float = 0.5  # more weight where the student is unsure
    disagreement_scale: float = 0.5  # more weight where the teacher disagrees
    max_weight: float = 3.0
    min_weight: float = 0.0
    clip_disagreement: float = 4.0  # guard against one pathological token

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def distillation_weight(student_entropy: float, disagreement: float, config: OPDConfig | None = None) -> float:
    """Turn-level distillation weight from student uncertainty and teacher disagreement.

    Disagreement is `mean(student_logprob - teacher_logprob)` on the sampled
    tokens: positive means the student was more confident than the teacher about
    what it just did, which is exactly where guidance is worth paying for.
    """
    config = config or OPDConfig()
    bounded = max(-config.clip_disagreement, min(config.clip_disagreement, disagreement))
    weight = (
        config.base_weight
        + config.uncertainty_scale * max(0.0, student_entropy)
        + config.disagreement_scale * max(0.0, bounded)
    )
    return float(max(config.min_weight, min(config.max_weight, weight)))


@dataclass
class MixSchedule:
    """OPD -> environment-PPO handover over the course of training.

    Early: the student cannot recover, so dense teacher guidance dominates.
    Late: the teacher only reads text and cannot know what the simulator will do,
    so the environment reward decides. The crossover is the project's core claim
    and therefore a configured, logged number rather than an implicit one.
    """

    total_steps: int
    opd_start: float = 1.0
    opd_end: float = 0.1
    env_start: float = 0.2
    env_end: float = 1.0
    warmup_fraction: float = 0.2  # fraction of training with opd held at opd_start
    mode: str = "cosine"  # cosine | linear | step

    def progress(self, step: int) -> float:
        if self.total_steps <= 1:
            return 1.0
        step = max(0, min(self.total_steps, step))
        warmup = self.warmup_fraction * self.total_steps
        if step <= warmup:
            return 0.0
        return min(1.0, (step - warmup) / max(1e-9, self.total_steps - warmup))

    def _interpolate(self, start: float, end: float, fraction: float) -> float:
        if self.mode == "linear":
            factor = fraction
        elif self.mode == "step":
            factor = 0.0 if fraction < 0.5 else 1.0
        else:  # cosine
            factor = 0.5 * (1.0 - math.cos(math.pi * fraction))
        return float(start + (end - start) * factor)

    def opd_weight(self, step: int) -> float:
        return self._interpolate(self.opd_start, self.opd_end, self.progress(step))

    def env_weight(self, step: int) -> float:
        return self._interpolate(self.env_start, self.env_end, self.progress(step))

    def weights(self, step: int) -> tuple[float, float]:
        return self.opd_weight(step), self.env_weight(step)


# ------------------------------------------------------------------- scoring


def sequence_logprobs(model: Any, input_ids: Any, attention_mask: Any = None) -> Any:
    """Per-position log-probability of the realized next token.

    Returns a tensor of shape `(batch, seq_len - 1)`: position i holds
    `log p(token_{i+1} | tokens_<=i)`.
    """
    import torch

    outputs = model(input_ids=input_ids, attention_mask=attention_mask)
    logits = outputs.logits[:, :-1, :].float()
    targets = input_ids[:, 1:]
    log_probs = torch.log_softmax(logits, dim=-1)
    return torch.gather(log_probs, dim=-1, index=targets.unsqueeze(-1)).squeeze(-1)


def sequence_entropy(model: Any, input_ids: Any, attention_mask: Any = None) -> Any:
    """Per-position predictive entropy, aligned with `sequence_logprobs`."""
    import torch

    outputs = model(input_ids=input_ids, attention_mask=attention_mask)
    logits = outputs.logits[:, :-1, :].float()
    log_probs = torch.log_softmax(logits, dim=-1)
    return -(log_probs.exp() * log_probs).sum(dim=-1)


@dataclass
class TurnDistillation:
    """Teacher signal for one scored turn."""

    turn_index: int
    reason: str
    span: tuple[int, int]
    student_logprob: float
    teacher_logprob: float
    disagreement: float
    student_entropy: float
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TeacherScorer:
    """Frozen teacher. Inference only; never trained, never sampled from."""

    def __init__(
        self,
        model_path: str,
        *,
        dtype: str = "bfloat16",
        device: str = "auto",
        config: OPDConfig | None = None,
        model: Any = None,
        tokenizer: Any = None,
        max_memory: dict[int, int] | None = None,
    ) -> None:
        self.model_path = model_path
        self.config = config or OPDConfig()
        if model is not None:
            self.model = model
            self.tokenizer = tokenizer
        else:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self.tokenizer = tokenizer or AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
            self.model = AutoModelForCausalLM.from_pretrained(
                model_path,
                torch_dtype=getattr(torch, dtype, torch.float32),
                device_map=device,
                max_memory=max_memory,
                trust_remote_code=True,
            )
        self.model.eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)

    def token_logprobs(self, input_ids: Any) -> Any:
        """Per-token log-probabilities in the shared convention.

        Index `i` holds `log p(token_{i+1} | tokens_<=i)`, the same layout
        `sequence_logprobs` returns, so the span arithmetic below is identical
        for the in-process and the served teacher.
        """
        import torch

        with torch.no_grad():
            values = sequence_logprobs(self.model, _as_tensor(input_ids, self.model.device))[0]
        return values.detach().float().cpu().numpy()

    def score_spans(
        self,
        input_ids: Any,
        spans: Sequence[tuple[int, int]],
        student_logprobs: Sequence[float],
        student_entropies: Sequence[float],
        reasons: Sequence[str],
        turn_indices: Sequence[int],
    ) -> list[TurnDistillation]:
        """Teacher log-probs on the student's sampled tokens for the given spans.

        `input_ids` is one tokenized episode; the teacher shares the student's
        tokenizer family, so the same ids address the same text.
        """
        teacher_logprobs = self.token_logprobs(input_ids)
        return _span_distillations(
            teacher_logprobs,
            spans,
            student_logprobs,
            student_entropies,
            reasons,
            turn_indices,
            self.config,
        )


class RemoteTeacherScorer:
    """Teacher served over HTTP and scored through an OpenAI-compatible API.

    The teacher is 30B: 60.8 GB in bf16. One copy per training rank cannot
    share cards with the student, so the teacher runs as its own process (see
    `training/scripts/serve_teacher.sh`) and the ranks ask it for
    log-probabilities over HTTP.

    The request carries token ids rather than text, so the teacher's tokenizer
    can never re-tokenize the student's tokens differently.
    """

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        api_key: str = "",
        timeout_s: float = 120.0,
        config: OPDConfig | None = None,
        tokenizer: Any = None,
    ) -> None:
        if not base_url:
            raise TeacherPlacementError(
                "teacher.backend=server needs teacher.base_url, e.g. http://127.0.0.1:8100/v1"
            )
        self.base_url = base_url.rstrip("/")
        self.model_path = model
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.config = config or OPDConfig()
        self.tokenizer = tokenizer

    # ------------------------------------------------------------------ http

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        import json
        import urllib.request

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    def token_logprobs(self, input_ids: Any) -> Any:
        """Per-token log-probabilities in the shared convention.

        The server returns `log p(token_j)` per prompt token; index `i` must
        hold `log p(token_{i+1})`, so the array is shifted by one.
        """
        import numpy as np

        ids = _as_id_list(input_ids)
        response = self._post(
            {
                "model": self.model,
                "prompt": ids,
                "max_tokens": 0,
                "echo": True,
                "logprobs": 0,
                "temperature": 0.0,
            }
        )
        raw = _token_logprobs_from_response(response)
        if len(raw) != len(ids):
            raise ValueError(
                f"teacher returned {len(raw)} log-probabilities for {len(ids)} tokens"
            )
        return np.array([_coerce_logprob(value) for value in raw[1:]], dtype=np.float64)

    def score_spans(
        self,
        input_ids: Any,
        spans: Sequence[tuple[int, int]],
        student_logprobs: Sequence[float],
        student_entropies: Sequence[float],
        reasons: Sequence[str],
        turn_indices: Sequence[int],
    ) -> list[TurnDistillation]:
        """Same arithmetic as the in-process scorer, over the served teacher."""
        teacher_logprobs = self.token_logprobs(input_ids)
        return _span_distillations(
            teacher_logprobs,
            spans,
            student_logprobs,
            student_entropies,
            reasons,
            turn_indices,
            self.config,
        )


# ------------------------------------------------------------------ helpers


def _as_id_list(input_ids: Any) -> list[int]:
    """Token ids as a flat list of ints, from a list or a tensor."""
    if hasattr(input_ids, "tolist"):
        input_ids = input_ids.tolist()
    if input_ids and isinstance(input_ids[0], (list, tuple)):
        input_ids = input_ids[0]
    return [int(token) for token in input_ids]


def _as_tensor(input_ids: Any, device: Any = None) -> Any:
    """Token ids as a `(1, seq_len)` tensor, from a list or a tensor."""
    import torch

    if isinstance(input_ids, torch.Tensor):
        tensor = input_ids
    else:
        tensor = torch.tensor([_as_id_list(input_ids)], dtype=torch.long)
    return tensor.to(device) if device is not None else tensor


def _coerce_logprob(value: Any) -> float:
    """A log-probability from any of the shapes the API may return."""
    if isinstance(value, dict):
        value = value.get("logprob")
    if value is None:
        return 0.0
    return float(value)


def _token_logprobs_from_response(response: dict[str, Any]) -> list[Any]:
    """Pull the per-token log-probabilities out of a completions response."""
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("teacher response carried no choices")
    choice = choices[0]
    values = (choice.get("logprobs") or {}).get("token_logprobs")
    if values is None:
        values = choice.get("prompt_logprobs")
    if values is None:
        raise ValueError("teacher response carried no token log-probabilities")
    return list(values)


def _span_distillations(
    teacher_logprobs: Any,
    spans: Sequence[tuple[int, int]],
    student_logprobs: Sequence[float],
    student_entropies: Sequence[float],
    reasons: Sequence[str],
    turn_indices: Sequence[int],
    config: OPDConfig,
) -> list[TurnDistillation]:
    """Turn-level distillation from per-token teacher log-probabilities.

    `teacher_logprobs[i]` is `log p(token_{i+1})`, so a token span
    `[start, end)` maps to the window `[start-1, end-1)`.
    """
    results: list[TurnDistillation] = []
    for index, (start, end) in enumerate(spans):
        window = teacher_logprobs[max(0, start - 1) : max(0, end - 1)]
        teacher_mean = float(window.mean()) if len(window) else 0.0
        student_mean = float(student_logprobs[index])
        entropy = float(student_entropies[index])
        disagreement = student_mean - teacher_mean
        results.append(
            TurnDistillation(
                turn_index=int(turn_indices[index]),
                reason=str(reasons[index]),
                span=(int(start), int(end)),
                student_logprob=student_mean,
                teacher_logprob=teacher_mean,
                disagreement=disagreement,
                student_entropy=entropy,
                weight=distillation_weight(entropy, disagreement, config),
            )
        )
    return results


def spread_max_memory(fraction: float = 0.0, *, reserve_bytes: int = 2 * GIB) -> dict[int, int] | None:
    """How much of each visible card the in-process teacher may use.

    `device_map="auto"` alone fills the first card until the model fits, which
    for a 60.8 GB teacher means card 0 alone and an OOM next to the student.
    Handing accelerate an explicit per-card budget spreads the weights over
    every card instead. `fraction` is the share of the *free* memory each card
    may give; 0 means "as much as is free".
    """
    import torch

    if not torch.cuda.is_available():
        return None
    budget: dict[int, int] = {}
    for index in range(torch.cuda.device_count()):
        free, _total = torch.cuda.mem_get_info(index)
        allowed = int(free * fraction) if fraction > 0 else int(free)
        budget[index] = max(0, allowed - reserve_bytes)
    return budget


__all__ = [
    "GateConfig",
    "GateDecision",
    "MixSchedule",
    "OPDConfig",
    "RemoteTeacherScorer",
    "TeacherPlacementError",
    "TeacherScorer",
    "TurnDistillation",
    "TurnFeatures",
    "TurnGate",
    "distillation_weight",
    "features_from_turn",
    "sequence_entropy",
    "sequence_logprobs",
    "spread_max_memory",
]
