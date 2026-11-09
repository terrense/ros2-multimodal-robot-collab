"""Local reliable tool runtime (Phase 2).

    from agent_harness.runtime import ToolClient, FakeBackend
    from agent_harness.contracts import CapabilityRegistry, ActionRequest

    client = ToolClient(FakeBackend(), CapabilityRegistry.load(repo_root))
    receipt = client.submit(request)          # queued
    final = client.wait(request.command_id)   # terminal receipt

Nothing here imports rclpy; the ROS 2 backend does, and it is imported
explicitly by whoever needs it.
"""

from __future__ import annotations

from .client import ToolBackend, ToolClient
from .control import RobotControl
from .fake_backend import Behaviour, Call, FakeBackend
from .ledger import DuplicateCommand, ExecutionLedger, LedgerError, LedgerRecord
from .policy import ExecutionPolicy, policy_for
from .scheduler import (
    CANCELLED,
    DONE,
    QUEUED,
    RUNNING,
    Job,
    ResourceScheduler,
    SchedulerClosed,
)

__all__ = [
    "CANCELLED",
    "DONE",
    "QUEUED",
    "RUNNING",
    "Behaviour",
    "Call",
    "DuplicateCommand",
    "ExecutionLedger",
    "ExecutionPolicy",
    "FakeBackend",
    "Job",
    "LedgerError",
    "LedgerRecord",
    "ResourceScheduler",
    "RobotControl",
    "SchedulerClosed",
    "ToolBackend",
    "ToolClient",
    "policy_for",
]
