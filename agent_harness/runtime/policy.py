"""Derive execution policy from a capability descriptor.

One rule per capability: exclusive on its resource. Arm commands serialise on
``arm``, base commands on ``base``, and a composite capability that moves both
takes ``episode``. There is no notion of "parallel within a resource": two arm
goals at once is not a scheduling question, it is a physical mistake.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_harness.contracts import CapabilityDescriptor


@dataclass(frozen=True)
class ExecutionPolicy:
    resource_key: str
    exclusive: bool = True
    timeout_s: float | None = None


def policy_for(
    descriptor: CapabilityDescriptor,
    *,
    default_timeout_s: float | None = None,
) -> ExecutionPolicy:
    timeout = descriptor.timeout_s if descriptor.timeout_s is not None else default_timeout_s
    return ExecutionPolicy(
        resource_key=descriptor.resource_key,
        exclusive=True,
        timeout_s=timeout,
    )
