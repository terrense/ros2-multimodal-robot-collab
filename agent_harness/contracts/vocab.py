"""Closed vocabularies shared by every execution path.

Two rules keep these useful:

1. Tokens are closed. An unknown unit, frame or failure code is rejected at
   parse time, because a silently accepted unknown unit is a physical mistake
   waiting for a robot.
2. Frames have one explicit extension point (``register_frame``) so a new robot
   adds a name deliberately instead of widening the vocabulary by accident.
"""

from __future__ import annotations

# Physical quantities. "unitless" covers counts, identifiers and booleans; it is
# not a place to park a physical quantity whose unit we have not defined.
UNIT_TOKENS = frozenset({"m", "rad", "s", "m/s", "rad/s", "N", "Nm", "unitless"})

# Principal frames of the workspace TF tree. Extend with register_frame().
KNOWN_FRAMES = frozenset(
    {
        "map",
        "odom",
        "base_link",
        "link_arm_base",
        "tool0",
        "camera_link",
        "gripper",
    }
)

_extra_frames: set[str] = set()

# Risks declared by skills/*/schema.json and agent_env/schemas/agent_skills.json
# that change the physical world. Everything else is observation only.
_MUTATING_RISKS = frozenset(
    {"arm_control", "arm_motion", "base_motion", "mission_motion", "mission_control"}
)


def register_frame(name: str) -> None:
    """Add a frame to the vocabulary for this process."""
    if not isinstance(name, str) or not name:
        raise ValueError("frame name must be a non-empty string")
    if any(character.isspace() for character in name):
        raise ValueError(f"frame name must not contain whitespace: {name!r}")
    _extra_frames.add(name)


def known_frames() -> frozenset[str]:
    return KNOWN_FRAMES | frozenset(_extra_frames)


def validate_frame(frame_id: str) -> str:
    if frame_id not in known_frames():
        raise ValueError(
            f"unknown frame {frame_id!r}; known frames: {sorted(known_frames())}"
        )
    return frame_id


def validate_units(
    units: dict[str, str], arguments: dict | None = None
) -> dict[str, str]:
    """Validate a unit map.

    Key grammar: a unit key is a **top-level key of the object it describes**
    (the request arguments or the snapshot payload). Dotted keys are literal
    names, not nested paths — ``{"pose.x": "m"}`` describes a top-level key
    called ``pose.x``, which almost nobody means.

    When ``arguments`` is supplied, every unit key must name an argument that is
    actually present. A unit attached to a non-existent argument is a typo that
    would otherwise travel all the way to a robot.
    """
    if not isinstance(units, dict):
        raise TypeError("units must be a mapping of argument name -> unit token")
    for path, token in units.items():
        if not isinstance(path, str) or not path:
            raise ValueError("unit key must be a non-empty string")
        if "." in path:
            raise ValueError(
                f"unit key {path!r} must be a top-level name, not a dotted path"
            )
        if token not in UNIT_TOKENS:
            raise ValueError(
                f"unknown unit {token!r} for {path!r}; allowed: {sorted(UNIT_TOKENS)}"
            )
        if arguments is not None and path not in arguments:
            raise ValueError(
                f"unit key {path!r} does not name an argument; "
                f"arguments are {sorted(arguments)}"
            )
    return dict(units)


def mutates_world(risk: str | None) -> bool:
    return risk in _MUTATING_RISKS


def resource_key_for_risk(risk: str | None) -> str:
    """Default exclusive resource for a capability.

    Base and arm are separate physical resources: they may move independently
    but each is exclusive with itself. Everything else shares the episode.
    """
    if risk == "base_motion":
        return "base"
    if risk in {"arm_control", "arm_motion"}:
        return "arm"
    return "episode"
