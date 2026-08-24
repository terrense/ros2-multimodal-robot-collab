"""Policies that drive the environment, plus the frozen OPD teacher.

    expert            scripted oracle -- generates SFT data, never a baseline
    rule_baseline     reconstruction of the deployed prompt + rule system
    HFPolicy          HuggingFace forward pass; smoke tests, CPU runs
    VLLMPolicy        batched rollout backend for PPO / OPD / evaluation
    TeacherScorer     frozen teacher; scores the student's own sampled tokens
"""

from .base import BasePolicy, GenerationConfig, Messages, Policy
from .llm_policy import HFPolicy, VLLMPolicy, load_policy, render_prompt
from .scripted_baseline import ExpertPolicy, RuleBaselinePolicy, RuleCoverage
from .teacher import (
    GateConfig,
    GateDecision,
    MixSchedule,
    OPDConfig,
    TeacherScorer,
    TurnDistillation,
    TurnFeatures,
    TurnGate,
    distillation_weight,
    features_from_turn,
)

__all__ = [
    "BasePolicy",
    "ExpertPolicy",
    "GateConfig",
    "GateDecision",
    "GenerationConfig",
    "HFPolicy",
    "Messages",
    "MixSchedule",
    "OPDConfig",
    "Policy",
    "RuleBaselinePolicy",
    "RuleCoverage",
    "TeacherScorer",
    "TurnDistillation",
    "TurnFeatures",
    "TurnGate",
    "VLLMPolicy",
    "distillation_weight",
    "features_from_turn",
    "load_policy",
    "render_prompt",
]
