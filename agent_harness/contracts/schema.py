"""JSON Schema documents for contract v1.

The dataclasses are the runtime authority; these documents are the on-disk and
on-the-wire form. Tests validate dataclass output against them, so the two can
never drift silently.

The documents are built by functions, not constants, because the frame
vocabulary has a runtime extension point (``vocab.register_frame``).
"""

from __future__ import annotations

from typing import Any

from .ids import MAX_ID_LENGTH
from .status import ExecutionStatus, FailureCode
from .types import CONTRACT_VERSION
from .vocab import known_frames

DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _identifier() -> dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_ID_LENGTH,
        "pattern": r"^\S+$",
    }


def _timestamp() -> dict[str, Any]:
    return {"type": ["number", "null"], "minimum": 0}


def _units() -> dict[str, Any]:
    from .vocab import UNIT_TOKENS

    return {
        "type": "object",
        "description": (
            "Top-level argument or payload name -> unit token. Keys are literal "
            "names, never dotted paths."
        ),
        "additionalProperties": {"enum": sorted(UNIT_TOKENS)},
        "propertyNames": {"minLength": 1, "pattern": r"^[^.]+$"},
    }


def _frame() -> dict[str, Any]:
    return {"enum": sorted(known_frames())}


def _capability_name() -> dict[str, Any]:
    return {"type": "string", "pattern": r"^[a-z][a-z0-9_-]*\.[a-z][a-z0-9_]*$"}


def _with_meta(title: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "$schema": DRAFT,
        "$id": f"https://example.invalid/contract-v1/{title}.schema.json",
        "title": title,
        "x-contract-version": CONTRACT_VERSION,
        **schema,
    }


def action_request_schema() -> dict[str, Any]:
    return _with_meta(
        "ActionRequest",
        {
            "type": "object",
            "required": ["episode_id", "command_id", "capability"],
            "additionalProperties": False,
            "properties": {
                "episode_id": _identifier(),
                "command_id": _identifier(),
                "capability": _capability_name(),
                "arguments": {"type": "object"},
                "requested_at": _timestamp(),
                "frame_id": {"anyOf": [_frame(), {"type": "null"}]},
                "units": _units(),
                "state_version": {"type": ["integer", "null"], "minimum": 0},
                "resource_key": {"type": "string", "minLength": 1, "pattern": r"^\S+$"},
                "timeout_s": {"type": ["number", "null"], "exclusiveMinimum": 0},
            },
        },
    )


def execution_receipt_schema() -> dict[str, Any]:
    return _with_meta(
        "ExecutionReceipt",
        {
            "type": "object",
            "required": ["episode_id", "command_id", "execution_status", "call_ok"],
            "additionalProperties": False,
            "properties": {
                "episode_id": _identifier(),
                "command_id": _identifier(),
                "execution_status": {"enum": [s.value for s in ExecutionStatus]},
                "call_ok": {"type": "boolean"},
                "physical_effect_verified": {"type": ["boolean", "null"]},
                "started_at": _timestamp(),
                "finished_at": _timestamp(),
                "failure_code": {
                    "anyOf": [
                        {"enum": [c.value for c in FailureCode]},
                        {"type": "null"},
                    ]
                },
                "detail": {"type": "string"},
                "snapshot_id": {
                    "anyOf": [_identifier(), {"type": "null"}]
                },
                "backend_ref": {
                    "anyOf": [_identifier(), {"type": "null"}],
                    "description": "Correlation id inside the backend, e.g. the ROS 2 goal uuid.",
                },
                "state_version": {"type": ["integer", "null"], "minimum": 0},
                "resource_key": {"type": "string", "minLength": 1, "pattern": r"^\S+$"},
                "result": {"type": "object"},
            },
            # The two invariants a non-Python reader can still check. The
            # dataclasses enforce more; these keep the wire format from
            # accepting a receipt Python would reject.
            "allOf": [
                {
                    "if": {
                        "properties": {"execution_status": {"const": "rejected"}},
                        "required": ["execution_status"],
                    },
                    "then": {
                        "properties": {"physical_effect_verified": {"const": False}},
                        "required": ["physical_effect_verified"],
                    },
                },
                {
                    "if": {
                        "properties": {"execution_status": {"const": "completed"}},
                        "required": ["execution_status"],
                    },
                    "then": {
                        "properties": {"call_ok": {"const": True}},
                        "required": ["call_ok"],
                    },
                },
            ],
        },
    )


def observation_snapshot_schema() -> dict[str, Any]:
    return _with_meta(
        "ObservationSnapshot",
        {
            "type": "object",
            "required": ["snapshot_id", "taken_at", "frame_id", "state_version"],
            "additionalProperties": False,
            "properties": {
                "snapshot_id": _identifier(),
                "taken_at": {"type": "number", "minimum": 0},
                "frame_id": _frame(),
                "state_version": {"type": "integer", "minimum": 0},
                "sensor_id": {"type": "string", "minLength": 1},
                "calibration_version": {"type": "string", "minLength": 1},
                "units": _units(),
                "payload": {"type": "object"},
            },
        },
    )


def task_outcome_schema() -> dict[str, Any]:
    return _with_meta(
        "TaskOutcome",
        {
            "type": "object",
            "properties": {
                "success": {"type": ["boolean", "null"]},
                "accepted": {"type": "boolean"},
                "retryable": {"type": "boolean"},
                "failure_category": {"type": ["string", "null"]},
                "detail": {"type": "string"},
            },
            # Mirrors the dataclass invariant: a retryable attempt never counts.
            "not": {"required": ["accepted", "retryable"], "properties": {
                "accepted": {"const": True}, "retryable": {"const": True}}},
        },
    )


def capability_descriptor_schema() -> dict[str, Any]:
    return _with_meta(
        "CapabilityDescriptor",
        {
            "type": "object",
            "required": ["name"],
            "additionalProperties": False,
            "properties": {
                "name": _capability_name(),
                "granularity": {"enum": ["atomic", "composite"]},
                "available_in": {
                    "type": "array",
                    "items": {"enum": ["l1", "ros2"]},
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "required_args": {"type": "array", "items": {"type": "string"}},
                "mutates_world": {"type": "boolean"},
                "resource_key": {"type": "string", "minLength": 1, "pattern": r"^\S+$"},
                "timeout_s": {"type": ["number", "null"], "exclusiveMinimum": 0},
                "risk": {"type": "string"},
            },
        },
    )


def all_schemas() -> dict[str, dict[str, Any]]:
    return {
        "ActionRequest": action_request_schema(),
        "ExecutionReceipt": execution_receipt_schema(),
        "ObservationSnapshot": observation_snapshot_schema(),
        "TaskOutcome": task_outcome_schema(),
        "CapabilityDescriptor": capability_descriptor_schema(),
    }
