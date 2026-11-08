"""Identifier construction and validation for contract v1.

Every submission carries three identities, and they answer different questions:

- ``episode_id``  one run of one task; stable across every command in it
- ``command_id``  idempotency identity of one submission; a retry of the same
                  intent reuses it, a new command never does
- ``snapshot_id`` content identity of one observation

An identifier is an opaque token: non-empty, no whitespace, bounded length.
Nothing downstream may parse meaning out of it.
"""

from __future__ import annotations

import uuid

MAX_ID_LENGTH = 128

_PREFIXES = {
    "episode": "ep",
    "command": "cmd",
    "snapshot": "snap",
}


def _new(kind: str) -> str:
    return f"{_PREFIXES[kind]}-{uuid.uuid4().hex[:12]}"


def new_episode_id() -> str:
    return _new("episode")


def new_command_id() -> str:
    return _new("command")


def new_snapshot_id() -> str:
    return _new("snapshot")


def validate_identifier(value: object, field: str) -> str:
    """Return ``value`` if it is a usable identifier, otherwise raise.

    Deliberately strict: whitespace and empty strings are rejected here rather
    than corrupting a receipt later.
    """
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{field} must not be empty")
    if len(value) > MAX_ID_LENGTH:
        raise ValueError(f"{field} exceeds {MAX_ID_LENGTH} characters")
    if any(character.isspace() for character in value):
        raise ValueError(f"{field} must not contain whitespace: {value!r}")
    return value
