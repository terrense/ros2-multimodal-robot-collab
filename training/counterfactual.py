"""Counterfactual recovery is only allowed on a verified matched-state clone.

An L1 environment can be deep-copied; the clone must reproduce the exact same
state hash before it may be used to score an alternative action. L2/L3 have no
verified restore, so counterfactual claims there are forbidden.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

COUNTERFACTUAL_LEVELS = frozenset({"l1"})


class UnmatchedClone(ValueError):
    """Raised when a clone does not reproduce the original state exactly."""


class CounterfactualNotAllowed(ValueError):
    """Raised when counterfactuals are requested at a level without verified restore."""


def state_hash(env: Any) -> str:
    """Hash the observable state a counterfactual must preserve."""
    if hasattr(env, "_observation"):
        observation = env._observation()
    else:  # pragma: no cover - defensive for environments without that hook
        observation = env.reset()
    payload = {
        "observation": observation.to_dict() if hasattr(observation, "to_dict") else observation,
        "phase": getattr(env.phase(), "value", None) if callable(getattr(env, "phase", None)) else None,
        "step_index": getattr(env, "step_index", None),
        "outcome": getattr(getattr(env, "outcome", None), "value", None),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def require_level(level: str) -> None:
    if level not in COUNTERFACTUAL_LEVELS:
        raise CounterfactualNotAllowed(
            f"counterfactuals are not allowed at level {level!r}: no verified matched restore"
        )


def verify_clone(original: Any, clone: Any) -> None:
    """Raise unless the clone reproduces the original state exactly."""
    if state_hash(clone) != state_hash(original):
        raise UnmatchedClone("clone state hash differs from the original")


def clone_verified(env: Any, *, level: str = "l1") -> Any:
    require_level(level)
    clone = copy.deepcopy(env)
    verify_clone(env, clone)
    return clone
