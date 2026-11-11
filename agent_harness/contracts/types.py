"""Contract v1 value types.

These are ROS 2-neutral, stdlib-only dataclasses. They describe a *submission*
(``ActionRequest``), its *evidence* (``ExecutionReceipt``), the world as seen at
one instant (``ObservationSnapshot``), the episode verdict (``TaskOutcome``) and
what a capability is allowed to do (``CapabilityDescriptor``).

Nothing here owns state. A receipt is evidence, not authority: the mission state
machine or the L1 environment remains the owner of the episode.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .ids import validate_identifier
from .status import ExecutionStatus, FailureCode, requires_failure_code
from .vocab import (
    mutates_world as risk_mutates_world,
)
from .vocab import (
    resource_key_for_risk,
    validate_frame,
    validate_units,
)

CONTRACT_VERSION = "contract-v1"

_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*$")
_GRANULARITIES = frozenset({"atomic", "composite"})
_PLATFORMS = frozenset({"l1", "ros2"})


def _check_capability(name: object) -> str:
    if not isinstance(name, str) or not _CAPABILITY_PATTERN.match(name):
        raise ValueError(f"capability must look like 'skill.action', got {name!r}")
    return name


def _check_optional_timestamp(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{field_name} must be a number or None")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return number


def _check_state_version(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("state_version must be a non-negative integer or None")
    return value


def _check_resource_key(value: object) -> str:
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise ValueError("resource_key must be a non-empty token without whitespace")
    return value


def _check_json_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{field_name} must be a mapping")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be JSON-serialisable: {error}") from error
    return dict(value)


@dataclass
class ActionRequest:
    """One submission. ``command_id`` is its idempotency identity."""

    episode_id: str
    command_id: str
    capability: str
    arguments: dict[str, Any] = field(default_factory=dict)
    requested_at: float | None = None
    frame_id: str | None = None
    units: dict[str, str] = field(default_factory=dict)
    state_version: int | None = None
    resource_key: str = "episode"
    timeout_s: float | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.episode_id, "episode_id")
        validate_identifier(self.command_id, "command_id")
        _check_capability(self.capability)
        self.arguments = _check_json_object(self.arguments, "arguments")
        self.requested_at = _check_optional_timestamp(self.requested_at, "requested_at")
        if self.frame_id is not None:
            validate_frame(self.frame_id)
        self.units = validate_units(self.units, arguments=self.arguments)
        self.state_version = _check_state_version(self.state_version)
        self.resource_key = _check_resource_key(self.resource_key)
        if self.timeout_s is not None:
            timeout = float(self.timeout_s)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout_s must be finite and positive")
            self.timeout_s = timeout

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionRequest:
        known = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class ExecutionReceipt:
    """Evidence for one submission.

    ``call_ok`` answers "did the invocation produce a well-formed result".
    ``physical_effect_verified`` answers "did the world observably change".
    Neither answers "did the task succeed"; that is ``TaskOutcome``.
    """

    episode_id: str
    command_id: str
    execution_status: ExecutionStatus
    call_ok: bool = False
    physical_effect_verified: bool | None = None
    started_at: float | None = None
    finished_at: float | None = None
    failure_code: FailureCode | None = None
    detail: str = ""
    snapshot_id: str | None = None
    backend_ref: str | None = None
    state_version: int | None = None
    resource_key: str = "episode"
    result: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.episode_id, "episode_id")
        validate_identifier(self.command_id, "command_id")
        self.execution_status = ExecutionStatus(self.execution_status)
        if self.failure_code is not None:
            self.failure_code = FailureCode(self.failure_code)
        if not isinstance(self.call_ok, bool):
            raise TypeError("call_ok must be a boolean")
        if self.physical_effect_verified is not None and not isinstance(
            self.physical_effect_verified, bool
        ):
            raise TypeError("physical_effect_verified must be a boolean or None")
        self.started_at = _check_optional_timestamp(self.started_at, "started_at")
        self.finished_at = _check_optional_timestamp(self.finished_at, "finished_at")
        if self.finished_at is not None:
            if self.started_at is None:
                raise ValueError("finished_at requires started_at")
            if self.finished_at < self.started_at:
                raise ValueError("finished_at precedes started_at")
        if self.snapshot_id is not None:
            validate_identifier(self.snapshot_id, "snapshot_id")
        if self.backend_ref is not None:
            validate_identifier(self.backend_ref, "backend_ref")
        self.state_version = _check_state_version(self.state_version)
        self.resource_key = _check_resource_key(self.resource_key)
        self.result = _check_json_object(self.result, "result")
        if not isinstance(self.detail, str):
            raise TypeError("detail must be a string")

        status = self.execution_status
        if requires_failure_code(status) and self.failure_code is None:
            raise ValueError(f"status {status.value!r} requires a failure_code")
        if status is ExecutionStatus.UNKNOWN and self.failure_code is None:
            self.failure_code = FailureCode.EXECUTION_UNCERTAIN
        if status in {
            ExecutionStatus.NOT_STARTED,
            ExecutionStatus.QUEUED,
            ExecutionStatus.REJECTED,
        } and self.call_ok:
            raise ValueError(f"status {status.value!r} cannot report call_ok=True")
        if status is ExecutionStatus.COMPLETED and not self.call_ok:
            raise ValueError("completed execution requires call_ok=True")
        if status is ExecutionStatus.REJECTED and self.physical_effect_verified is not False:
            raise ValueError("rejected execution cannot claim a physical effect")

    @property
    def effect_established(self) -> bool:
        """True only when the world was observed to change (or not)."""
        return self.physical_effect_verified is not None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["execution_status"] = self.execution_status.value
        data["failure_code"] = None if self.failure_code is None else self.failure_code.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionReceipt:
        known = {field.name for field in cls.__dataclass_fields__.values()}
        payload = {key: value for key, value in data.items() if key in known}
        payload["execution_status"] = ExecutionStatus(payload["execution_status"])
        if payload.get("failure_code") is not None:
            payload["failure_code"] = FailureCode(payload["failure_code"])
        return cls(**payload)


@dataclass
class ObservationSnapshot:
    """The world as observed at one instant, with the identity to prove it.

    A snapshot is only usable if it says *when* it was taken, *which sensor*
    produced it and *which calibration* it was built with; otherwise an action
    could be planned against a stale or misaligned view.
    """

    snapshot_id: str
    taken_at: float
    frame_id: str
    state_version: int
    sensor_id: str = "unknown"
    calibration_version: str = "uncalibrated"
    units: dict[str, str] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_identifier(self.snapshot_id, "snapshot_id")
        taken = _check_optional_timestamp(self.taken_at, "taken_at")
        if taken is None:
            raise ValueError("taken_at is required")
        self.taken_at = taken
        validate_frame(self.frame_id)
        version = _check_state_version(self.state_version)
        if version is None:
            raise ValueError("state_version is required")
        if not isinstance(self.sensor_id, str) or not self.sensor_id.strip():
            raise ValueError("sensor_id must be a non-empty string")
        if not isinstance(self.calibration_version, str) or not self.calibration_version.strip():
            raise ValueError("calibration_version must be a non-empty string")
        self.state_version = version
        self.payload = _check_json_object(self.payload, "payload")
        self.units = validate_units(self.units, arguments=self.payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservationSnapshot:
        known = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in known})

    def age(self, now_s: float) -> float:
        """Seconds between capture and ``now``. Negative means clock skew."""
        return float(now_s) - self.taken_at

    def is_fresh(self, now_s: float, max_age_s: float) -> bool:
        """True when the snapshot is usable at ``now``.

        A snapshot from the future is not fresh either: that means the two
        clocks disagree and any correlation with it is unsafe.
        """
        age = self.age(now_s)
        return 0.0 <= age <= max_age_s

    def require_fresh(self, now_s: float, max_age_s: float) -> None:
        if not self.is_fresh(now_s, max_age_s):
            raise StaleSnapshot(
                f"snapshot {self.snapshot_id!r} age {self.age(now_s):.3f}s "
                f"exceeds {max_age_s:.3f}s (or comes from the future)"
            )


class StaleSnapshot(ValueError):
    """Raised when a snapshot is too old (or mis-timed) to act on."""


@dataclass
class TaskOutcome:
    """Episode verdict. ``success=None`` means unknown, not False."""

    success: bool | None = None
    accepted: bool = True
    retryable: bool = False
    failure_category: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.success is not None and not isinstance(self.success, bool):
            raise TypeError("success must be a boolean or None")
        if not isinstance(self.accepted, bool) or not isinstance(self.retryable, bool):
            raise TypeError("accepted and retryable must be booleans")
        if self.retryable and self.accepted:
            # A retryable attempt must not enter the evaluation denominator.
            raise ValueError("a retryable outcome cannot be accepted")
        if self.failure_category is not None and not isinstance(self.failure_category, str):
            raise TypeError("failure_category must be a string or None")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskOutcome:
        known = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class CapabilityDescriptor:
    """What a capability is, where it exists, and what it touches.

    ``granularity="composite"`` means one submission covers several observable
    phases; the executor must still emit progress for it.

    ``model_visible`` separates two questions that were conflated in one:
    whether the *runtime* may dispatch this, and whether the *model* should be
    offered it. `/skills/pick_and_place` is a real robot capability with its own
    action server, and the runtime needs it (the L2 adapter and the calibration
    probes drive it) -- but a model that can call it directly would bypass the
    mission state machine's own sequence. So it is dispatchable and not offered.
    """

    name: str
    granularity: str = "atomic"
    available_in: frozenset[str] = field(default_factory=lambda: frozenset({"ros2"}))
    required_args: tuple[str, ...] = ()
    mutates_world: bool = False
    resource_key: str = "episode"
    timeout_s: float | None = None
    risk: str = "unknown"
    model_visible: bool = True

    def __post_init__(self) -> None:
        _check_capability(self.name)
        if self.granularity not in _GRANULARITIES:
            raise ValueError(f"granularity must be one of {sorted(_GRANULARITIES)}")
        platforms = frozenset(self.available_in)
        unknown = platforms - _PLATFORMS
        if unknown or not platforms:
            raise ValueError(
                f"available_in must be a non-empty subset of {sorted(_PLATFORMS)}, "
                f"got {sorted(platforms)}"
            )
        self.available_in = platforms
        self.required_args = tuple(self.required_args)
        if not all(isinstance(arg, str) and arg for arg in self.required_args):
            raise ValueError("required_args must be non-empty strings")
        if not isinstance(self.mutates_world, bool):
            raise TypeError("mutates_world must be a boolean")
        if not isinstance(self.model_visible, bool):
            raise TypeError("model_visible must be a boolean")
        self.resource_key = _check_resource_key(self.resource_key)
        if self.timeout_s is not None:
            timeout = float(self.timeout_s)
            if not math.isfinite(timeout) or timeout <= 0:
                raise ValueError("timeout_s must be finite and positive")
            self.timeout_s = timeout

    def supports(self, platform: str) -> bool:
        return platform in self.available_in

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["available_in"] = sorted(self.available_in)
        data["required_args"] = list(self.required_args)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityDescriptor:
        known = {field.name for field in cls.__dataclass_fields__.values()}
        payload = {key: value for key, value in data.items() if key in known}
        if "available_in" in payload:
            payload["available_in"] = frozenset(payload["available_in"])
        return cls(**payload)

    @classmethod
    def from_schema_action(
        cls,
        skill: str,
        action: str,
        spec: dict[str, Any],
        *,
        platform: str,
        composite: bool = False,
    ) -> CapabilityDescriptor:
        """Build a descriptor from either schema dialect.

        Both ``skills/*/schema.json`` and ``agent_env/schemas/agent_skills.json``
        use ``required``, ``risk`` and ``timeout_sec``; that overlap is enough.
        """
        risk = spec.get("risk")
        return cls(
            name=f"{skill}.{action}",
            granularity="composite" if composite else "atomic",
            available_in=frozenset({platform}),
            required_args=tuple(spec.get("required") or ()),
            mutates_world=risk_mutates_world(risk),
            resource_key=resource_key_for_risk(risk),
            timeout_s=spec.get("timeout_sec"),
            risk=str(risk or "unknown"),
            # A schema may say "the runtime may do this, the model should not".
            model_visible=bool(spec.get("model_visible", True)),
        )
