"""L1 environment behind the ToolBackend interface.

The closed loop (Phase 5) and the ROS backend (Phase 2) both speak receipts.
This adapter gives the L1 simulator the same voice, so L1 and L2 metrics can be
computed from identical structures. ``agent_env`` is imported lazily: the
runtime package must stay free of training-side imports.
"""

from __future__ import annotations

from typing import Any

from agent_harness.contracts import ActionRequest, ExecutionReceipt, receipt_from_step_result


class L1Backend:
    def __init__(self, env: Any) -> None:
        self._env = env

    def execute(self, request: ActionRequest) -> ExecutionReceipt:
        from agent_env.protocol import Action

        skill, _, action = request.capability.partition(".")
        self._env.step(
            Action(skill=skill, action=action, args=dict(request.arguments))
        )
        return receipt_from_step_result(
            self._env.last_result,
            episode_id=request.episode_id,
            command_id=request.command_id,
            resource_key=request.resource_key,
        )

    def close(self) -> None:
        """The environment is owned by the caller; nothing to release here."""
