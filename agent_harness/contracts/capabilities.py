"""Capability registry: one descriptor per action name, both platforms merged.

The two paths currently carry two schema dialects with overlapping but unequal
action sets: 8 skills actions are declared (6 of them agent-callable) and 13 L1
actions exist, with 5 shared names. This module does not rename anything; it
produces the union of the callable names and records on which platforms each
one exists. Actions declared ``callable: false`` are perception internals and
are deliberately absent.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from .types import CapabilityDescriptor

# The only composite capability in v1: it wraps the whole delivery inside the
# mission state machine. Decomposing it is a later decision, not an accident.
_COMPOSITE_ACTIONS = frozenset({("mission-control", "deliver_tool")})


class UnsupportedCapability(KeyError):
    """Raised when a capability is unknown or unavailable on the target platform."""


class CapabilityRegistry:
    def __init__(self, descriptors: Iterable[CapabilityDescriptor] = ()) -> None:
        self._by_name: dict[str, CapabilityDescriptor] = {}
        for descriptor in descriptors:
            self.add(descriptor)

    def add(self, descriptor: CapabilityDescriptor) -> None:
        """Insert, or merge platforms when the name already exists."""
        existing = self._by_name.get(descriptor.name)
        if existing is None:
            self._by_name[descriptor.name] = descriptor
            return
        if existing.granularity != descriptor.granularity:
            raise ValueError(
                f"granularity conflict for {descriptor.name}: "
                f"{existing.granularity} vs {descriptor.granularity}"
            )
        required = tuple(dict.fromkeys((*existing.required_args, *descriptor.required_args)))
        self._by_name[descriptor.name] = CapabilityDescriptor(
            name=existing.name,
            granularity=existing.granularity,
            available_in=existing.available_in | descriptor.available_in,
            required_args=required,
            mutates_world=existing.mutates_world or descriptor.mutates_world,
            resource_key=existing.resource_key,
            timeout_s=existing.timeout_s or descriptor.timeout_s,
            risk=existing.risk,
            # Visible to the model only if *every* source says so.
            model_visible=existing.model_visible and descriptor.model_visible,
        )

    def get(self, name: str) -> CapabilityDescriptor:
        try:
            return self._by_name[name]
        except KeyError as error:
            raise UnsupportedCapability(f"unknown capability {name!r}") from error

    def require(self, name: str, platform: str) -> CapabilityDescriptor:
        descriptor = self.get(name)
        if not descriptor.supports(platform):
            raise UnsupportedCapability(
                f"capability {name!r} is not available on platform {platform!r}; "
                f"available on {sorted(descriptor.available_in)}"
            )
        return descriptor

    def for_platform(self, platform: str) -> list[CapabilityDescriptor]:
        return sorted(
            (d for d in self._by_name.values() if d.supports(platform)),
            key=lambda d: d.name,
        )

    def model_visible_for(self, platform: str) -> list[CapabilityDescriptor]:
        """The capabilities a *model* should be offered.

        Distinct from `for_platform`: a capability can be dispatchable by the
        runtime and still not offered to the model, because offering it would
        let the model bypass a sequence the runtime owns.
        """
        return sorted(
            (
                d
                for d in self._by_name.values()
                if d.supports(platform) and d.model_visible
            ),
            key=lambda d: d.name,
        )

    def describe_for(self, platform: str) -> list[str]:
        """One model-facing line per capability, derived from the registry.

        Nothing here is hard-coded: adding an action to a schema file makes it
        appear, and a capability absent on the platform never does.
        """
        lines = []
        for descriptor in self.model_visible_for(platform):
            args = ", ".join(descriptor.required_args) or "-"
            lines.append(
                f"{descriptor.name}({args}) "
                f"[{descriptor.granularity}, risk={descriptor.risk}, "
                f"mutates={str(descriptor.mutates_world).lower()}]"
            )
        return lines

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    @classmethod
    def load(cls, repo_root: Path) -> CapabilityRegistry:
        """Union of ``skills/*/schema.json`` and ``agent_env/schemas/agent_skills.json``."""
        registry = cls()
        for descriptor in _load_ros_descriptors(repo_root):
            registry.add(descriptor)
        for descriptor in _load_l1_descriptors(repo_root):
            registry.add(descriptor)
        return registry


def _load_ros_descriptors(repo_root: Path) -> Iterable[CapabilityDescriptor]:
    for schema_path in sorted((repo_root / "skills").glob("*/schema.json")):
        with schema_path.open("r", encoding="utf-8") as handle:
            schema = json.load(handle)
        skill = str(schema.get("name") or schema_path.parent.name)
        for action, spec in (schema.get("actions") or {}).items():
            if spec.get("callable", True) is False:
                continue
            yield CapabilityDescriptor.from_schema_action(
                skill,
                action,
                spec,
                platform="ros2",
                composite=(skill, action) in _COMPOSITE_ACTIONS,
            )


def _load_l1_descriptors(repo_root: Path) -> Iterable[CapabilityDescriptor]:
    path = repo_root / "agent_env" / "schemas" / "agent_skills.json"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    for name, spec in (schema.get("actions") or {}).items():
        skill, _, action = name.partition(".")
        if not action:
            raise ValueError(f"L1 action {name!r} is not in 'skill.action' form")
        yield CapabilityDescriptor.from_schema_action(
            skill,
            action,
            spec,
            platform="l1",
            composite=(skill, action) in _COMPOSITE_ACTIONS,
        )
