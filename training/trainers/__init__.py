"""Trainer internals for the online stages.

    experience  rollout records -> tokenized sequences with turn credit on the
                token spans that earned it
    ppo         clipped policy loss, value loss, KL penalty
    opd         on-policy distillation loss and the OPD -> PPO mix

The loss functions are plain tensor functions so they can be checked on CPU; the
orchestration around them is what needs the cluster.
"""

from .experience import (
    EpisodeExperience,
    ExperienceStats,
    SequenceExperience,
    TurnExperience,
    attach_credit,
    build_experience,
    summarize,
    whiten_batch,
)

__all__ = [
    "EpisodeExperience",
    "ExperienceStats",
    "SequenceExperience",
    "TurnExperience",
    "attach_credit",
    "build_experience",
    "summarize",
    "whiten_batch",
]
