"""Operator control paths: cancel, pause, stop.

These exist so that stopping the robot never depends on the model choosing a
tool. They are called from the CLI or a supervisor process, not from a prompt.

Nothing here claims to be a hardware emergency stop. ``system_stop`` is a topic
message the arm server consumes; whether the controller honours it is the
controller's business. A real E-stop is a hardware circuit and a separate
concern.
"""

from __future__ import annotations

from agent_harness.contracts import ActionRequest, ExecutionReceipt, new_command_id

from .client import ToolClient

ARM_CONTROL = "manipulation.publish_arm_control"


class RobotControl:
    def __init__(self, client: ToolClient, *, episode_id: str, operator: str) -> None:
        if not operator:
            raise ValueError("operator name is required for an auditable control action")
        self._client = client
        self._episode_id = episode_id
        self._operator = operator

    def cancel(self, command_id: str) -> bool:
        """Cancel a queued command. A running command is left alone."""
        return self._client.cancel(command_id)

    def _arm_command(self, command: str, *, emergency: bool) -> ExecutionReceipt:
        request = ActionRequest(
            episode_id=self._episode_id,
            command_id=new_command_id(),
            capability=ARM_CONTROL,
            arguments={
                "command": command,
                "operator_id": self._operator,
                "source": "operator_control",
                "confidence": 1.0,
                "emergency": emergency,
            },
        )
        return self._client.submit(request)

    def pause(self) -> ExecutionReceipt:
        """Ask the arm to pause. Not an E-stop; a request the controller may refuse."""
        return self._arm_command("arm_pause", emergency=False)

    def stop(self, *, reason: str) -> ExecutionReceipt:
        """Request a stop, then fail closed.

        The fail-closed flag is set after the request is queued, so the stop
        itself is never blocked by the flag it is about to raise. Every later
        side-effecting submission is refused until an operator clears it.
        """
        if not reason:
            raise ValueError("stop requires a reason")
        receipt = self._arm_command("system_stop", emergency=True)
        self._client.set_fail_closed(f"operator stop by {self._operator}: {reason}")
        return receipt

    def resume(self, *, note: str, operator: str | None = None) -> None:
        """Clear fail-closed. Requires a named operator and a note."""
        self._client.clear_fail_closed(operator=operator or self._operator, note=note)
