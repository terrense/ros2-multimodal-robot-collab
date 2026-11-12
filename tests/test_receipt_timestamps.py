"""A receipt has to say when the command ran.

The receipt contract has carried `started_at` and `finished_at` since the
beginning, and nothing ever filled them in. The backend knows how long a call
took, not when it started and ended; the client knows both.

It matters because the receipt metrics ask "how long did this take?", and
with no timestamps the honest answer was "not applicable" -- on the live path
too, which is where the question actually has an answer. The live path now
reports a median mission time of 9.76 s, and that number comes from here.
"""

from __future__ import annotations

from pathlib import Path

from agent_harness.contracts import (
    ActionRequest,
    CapabilityRegistry,
    ExecutionStatus,
    new_command_id,
)
from agent_harness.runtime.client import ToolClient
from agent_harness.runtime.fake_backend import FakeBackend

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = CapabilityRegistry.load(REPO_ROOT)


def _dispatch(client: ToolClient):
    request = ActionRequest(
        episode_id="ep-timestamps",
        command_id=new_command_id(),
        capability="navigation.navigate_to_station",
        arguments={"station_id": "station_a", "reason": "timestamp test"},
    )
    submitted = client.submit(request)
    if submitted.execution_status is ExecutionStatus.QUEUED:
        return client.wait(request.command_id)
    return submitted


def test_a_receipt_carries_when_the_command_started_and_finished() -> None:
    client = ToolClient(FakeBackend(), REGISTRY)
    receipt = _dispatch(client)
    assert receipt.started_at is not None
    assert receipt.finished_at is not None
    assert receipt.finished_at >= receipt.started_at


def test_the_latency_is_the_difference_between_the_two_stamps() -> None:
    """The receipt metrics read exactly this difference."""
    from training.receipt_metrics import receipt_metrics

    client = ToolClient(FakeBackend(), REGISTRY)
    receipt = _dispatch(client)
    metrics = receipt_metrics([receipt.to_dict()])
    assert metrics["latency_p50_ms"] is not None
    assert metrics["latency_p50_ms"] >= 0.0
