#!/usr/bin/env python3
"""Run the calibration programmes against the live stack and record what happened.

    # with the Gazebo stack up and the scenes arranged:
    python -m agent_env.scripts.run_calibration --out runs/l2_measurements.json
    python -m agent_env.scripts.calibrate_l2 --from-log runs/l2_measurements.json

The runner executes the programmes in `agent_env/l2_calibration.py` through the
live stack, and writes a measurement log. `calibrate_l2 --from-log` turns that
log into `benchmarks/calibration_v1.json`.

Two rules, both enforced rather than documented:

* a family whose scene is not arranged is **not measured**, and the reason is
  printed. It is not filled with the L1 value or with a guess.
* a family measured fewer than `--min-samples` times cannot become calibrated;
  the manifest says `insufficient_samples`, whatever the rate.

`--plan` prints what would run and what each probe does, without touching the
robot.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from agent_env.calibration import MIN_SAMPLES
from agent_env.calibration_runner import (
    CalibrationRunner,
    describe,
    measurements_to_rows,
    summarise_skipped,
)
from agent_env.faults import FaultType
from agent_env.l2_calibration import plan


class RosStack:
    """The live stack, through the runtime client."""

    def __init__(self, timeout_s: float = 120.0, ledger: Path | None = None) -> None:
        from agent_harness.contracts import CapabilityRegistry
        from agent_harness.runtime import ExecutionLedger, ToolClient
        from agent_harness.runtime.ros_backend import RosBackend
        from agent_harness.scripts.ros_tool_executor import RosToolExecutor, SkillRegistry

        repo_root = Path(__file__).resolve().parents[2]
        registry = SkillRegistry.load(repo_root / "skills")
        # Concurrent: the cancel probe raises a stop while a motion is in
        # flight, and a serial executor would have two threads spinning one
        # node to do it.
        self._executor = RosToolExecutor(timeout_sec=timeout_s, concurrent=True)
        self._ledger = ExecutionLedger(ledger) if ledger is not None else None
        self._client = ToolClient(
            RosBackend(self._executor, registry, concurrent=True),
            CapabilityRegistry.load(repo_root),
            default_timeout_s=timeout_s,
            max_workers=4,
            ledger=self._ledger,
        )
        #: Buffers for the topics the probes read. The subscriptions live for
        #: as long as the stack does.
        #:
        #: They used to be created and destroyed per read, and destroying a
        #: subscription the background executor was about to take from its wait
        #: list raised `InvalidHandle` *inside the executor's thread*. That
        #: killed the spin loop, and every action dispatched after it never
        #: came back. One subscription per topic, created once, is both safer
        #: and cheaper.
        self._detections: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._detection_subscription: Any = None
        self._event_subscription: Any = None

    def call(self, capability: str, arguments: dict[str, Any]) -> Any:
        from agent_harness.contracts import ActionRequest, new_command_id

        filtered = {key: value for key, value in arguments.items() if key != "timeout_s"}
        request = ActionRequest(
            episode_id="l2-calibration",
            command_id=new_command_id(),
            capability=capability,
            arguments=filtered,
            # A short timeout is how the nav_timeout probe *produces* its fault.
            # The client caps this at the capability's own limit, so asking for
            # 1 s on a 120 s navigation is the caller asking for less, not for
            # more than the capability allows.
            timeout_s=arguments.get("timeout_s"),
            resource_key=_resource_for(capability),
        )
        submitted = self._client.submit(request)
        if str(getattr(submitted.execution_status, "value", submitted.execution_status)) == "queued":
            return self._client.wait(request.command_id)
        return submitted

    @staticmethod
    def receipt_status(receipt: Any) -> str:
        status = getattr(receipt, "execution_status", "")
        return str(getattr(status, "value", status))

    def read_detections(self, timeout_s: float = 3.0) -> list[dict[str, Any]]:
        """What the perception nodes published.

        A detection is a topic, not a capability: there is no "detect this
        tool" to call, so a probe that has to know what the detector saw has
        to read what it published.
        """
        from robot_collab_interfaces.msg import ToolDetection

        node = self._executor.node
        if self._detection_subscription is None:
            def on_detection(msg: Any) -> None:
                self._detections.append(
                    {
                        "tool_id": str(msg.tool_id),
                        "label": str(msg.label),
                        "confidence": float(msg.confidence),
                        "source_frame": str(msg.source_frame),
                        "detector_id": str(msg.detector_id),
                    }
                )

            self._detection_subscription = node.create_subscription(
                ToolDetection, "/perception/tool_detections", on_detection, 10
            )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not self._detections:
            self._wait_for_the_executor()
        received = list(self._detections)
        self._detections.clear()
        return received

    def read_mission_events(
        self, timeout_s: float = 5.0, until_state: str | None = None
    ) -> list[dict[str, Any]]:
        """The mission's published progress events.

        The mission announces its phases on `/mission/events`, which is how a
        probe knows when the arm is actually moving -- a cancel raised before
        the arm has started has nothing to stop.
        """
        from robot_collab_interfaces.msg import MissionEvent

        node = self._executor.node
        if self._event_subscription is None:
            def on_event(msg: Any) -> None:
                self._events.append(
                    {
                        "state": str(msg.state),
                        "detail": str(msg.detail),
                        "progress": float(msg.progress),
                    }
                )

            self._event_subscription = node.create_subscription(
                MissionEvent, "/mission/events", on_event, 20
            )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if until_state and any(event["state"] == until_state for event in self._events):
                break
            self._wait_for_the_executor()
        received = list(self._events)
        self._events.clear()
        return received

    def _wait_for_the_executor(self) -> None:
        """Give the background executor a chance to deliver a message."""
        if self._executor.concurrent:
            # The background executor is already spinning this node.
            time.sleep(0.05)
        else:
            self._executor.rclpy.spin_once(self._executor.node, timeout_sec=0.1)

    def reconcile(self, command_id: str, note: str) -> None:
        """Accept an outcome the runtime could not establish.

        The ledger blocks every later side effect while a command is
        unresolved, because a timeout means the effect is not known and on a
        robot somebody has to look. The nav_timeout probe produces that
        timeout on purpose, so it acknowledges it -- with a note that says
        which probe did it and why.
        """
        if self._ledger is None or not command_id:
            return
        try:
            self._ledger.acknowledge_unknown(
                command_id, operator="calibration", note=note
            )
        except Exception as error:  # noqa: BLE001 - nothing was unresolved
            print(f"reconcile {command_id}: {error}")

    def close(self) -> None:
        self._client.shutdown(30.0)
        if self._ledger is not None:
            self._ledger.close()
        self._executor.close()


def _resource_for(capability: str) -> str:
    """The exclusive resource a capability competes for.

    The runtime overrides this with the descriptor's own resource key -- the
    descriptor owns it, not the caller -- but it is kept here so the two do
    not drift apart in the reader's head.
    """
    if capability.startswith("navigation."):
        return "base"
    if capability == "manipulation.publish_arm_control":
        return "arm_control"
    if capability.startswith("manipulation."):
        return "arm"
    return "episode"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("runs/l2_measurements.json"))
    parser.add_argument("--samples", type=int, default=MIN_SAMPLES)
    parser.add_argument("--family", action="append", default=None)
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--tool-id", default="hex_key_3mm", help="the tool the stack is configured for")
    parser.add_argument("--pickup-station", default="tool_shelf")
    parser.add_argument("--delivery-station", default="station_a")
    parser.add_argument("--operator-id", default="operator_001")
    parser.add_argument(
        "--no-scenes",
        action="store_true",
        help="do not arrange the world; the scene-dependent families cannot be measured",
    )
    parser.add_argument("--plan", action="store_true", help="print the programmes and dispatch nothing")
    args = parser.parse_args()

    families = [FaultType(name) for name in args.family] if args.family else None

    if args.plan:
        for entry in plan(families):
            print(f"[{entry['family']}] {entry['question']}")
            print(f"  success when: {entry['success_event']}")
            if entry["scene_setup"]:
                print("  scene setup: " + "; ".join(entry["scene_setup"]))
            print(f"  samples: {args.samples}")
        print()
        print(f"tool={args.tool_id} pickup={args.pickup_station} "
              f"delivery={args.delivery_station} operator={args.operator_id}")
        return

    scenes = None
    if not args.no_scenes:
        # The scene driver places static props and sets node parameters. It
        # never dispatches a capability, so it cannot move the robot.
        from agent_env.calibration_scene import CalibrationSceneDriver

        scenes = CalibrationSceneDriver()

    stack = RosStack(timeout_s=args.timeout_sec, ledger=args.ledger)
    try:
        runner = CalibrationRunner(
            stack,
            min_samples=args.samples,
            tool_id=args.tool_id,
            pickup_station=args.pickup_station,
            delivery_station=args.delivery_station,
            operator_id=args.operator_id,
            scenes=scenes,
        )
        measurements = runner.run(families)
        unmeasured = runner.reason_unmeasured(families)
    finally:
        stack.close()
        if scenes is not None:
            scenes.close()

    payload = {
        "version": "l2_measurements_v1",
        "min_samples": args.samples,
        "families": measurements_to_rows(measurements),
        # Three reasons are kept three: the world is not arranged, the stack is
        # not answering, or the attempt came back clean. Collapsing them would
        # hide which one to fix.
        "unmeasured": unmeasured,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    print("measured:")
    print(describe(measurements))
    print("\nnot measured:")
    print(summarise_skipped(unmeasured))
    print(f"\n-> {args.out}")
    print(
        "\nnext: python -m agent_env.scripts.calibrate_l2 "
        f"--from-log {args.out} --out benchmarks/calibration_v1.json"
    )


if __name__ == "__main__":
    main()
