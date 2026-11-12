#!/usr/bin/env python3
"""Run the wire-fault experiment against the live stack and decide the criteria.

    sudo python3 scripts/wire_fault_check.py dropped_result --ledger /tmp/wire.db
    python3 scripts/wire_fault_check.py dropped_result --dry-run

The criteria are in [`docs/specs/wire-faults.md`](../docs/specs/wire-faults.md).
This script executes them.

The run has a shape, and the shape is the point:

    1. a clean baseline dispatch          (WF-C3)
    2. apply the fault, then probe it     (WF-C1)
    3. the faulted dispatch
    4. clear the fault, then probe it     (WF-C2)
    5. a recovery dispatch after the fault is gone   (WF-L2)
    6. audit the ledger                    (WF-S1..S6, WF-L1)

Four rules, and they are the reason this script exists:

1. **The interface is restored in a `finally`.** A crash that leaves a netem
   rule behind makes every later experiment on the machine wrong, and nobody
   attributes it to this tool.
2. **Nothing is dispatched before the fault is in place.** Measuring the clean
   path and reporting it as the faulted one is worse than no run.
3. **The verdict is three-valued.** A failed set-up criterion makes the run
   **void**, not failed: a misconfigured injection looks exactly like a system
   that survived a fault it never saw.
4. **The verdict comes from the ledger, not from this script.** Whether a
   command was dispatched twice is a fact about the ledger; this harness only
   reads it.

Requires root for `tc`, a running stack, and a person: the commands move the
robot. `--dry-run` prints exactly what would be executed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_harness.runtime.wire_faults import (  # noqa: E402
    SCENARIOS,
    LedgerView,
    RunFacts,
    Verdict,
    build_commands,
    evaluate_wire_fault,
    probe_netem,
    render,
)


def _run(commands: list[list[str]], *, dry_run: bool) -> None:
    for command in commands:
        print("  $ " + " ".join(command))
        if dry_run:
            continue
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        # `tc qdisc del` fails when there is nothing to delete; that is fine.
        if result.returncode != 0 and "Cannot delete" not in (result.stderr or ""):
            print(f"    (exit {result.returncode}) {result.stderr.strip()}")


def _require_tc() -> str:
    binary = shutil.which("tc")
    if binary is None:
        raise SystemExit(
            "`tc` is not installed. Install iproute2:\n  sudo apt install iproute2"
        )
    return binary


def _dispatch(client, capability: str, arguments: dict, timeout_s: float):
    """One command through the runtime, and the receipt it produced.

    The wall clock comes back too: a lost packet is retransmitted rather than
    dropped, so the way a fault shows up on a reliable transport is as time.
    Without the timing there is no way to tell "the system survived the fault"
    from "the fault never reached the transport".
    """
    from agent_harness.contracts import ActionRequest, new_command_id

    request = ActionRequest(
        episode_id="wire-fault",
        command_id=new_command_id(),
        capability=capability,
        arguments=dict(arguments),
        timeout_s=timeout_s,
    )
    started = time.monotonic()
    submitted = client.submit(request)
    status = str(getattr(submitted.execution_status, "value", submitted.execution_status))
    receipt = client.wait(request.command_id) if status == "queued" else submitted
    elapsed = time.monotonic() - started
    observed = str(getattr(receipt.execution_status, "value", receipt.execution_status))
    return request.command_id, observed, receipt, elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--interface", default="lo", help="network interface (default: lo)")
    parser.add_argument("--ledger", type=Path, default=Path("/tmp/wire_fault.db"))
    parser.add_argument("--capability", default=None, help="override the scenario's capability")
    parser.add_argument("--arguments", default=None, help="JSON arguments; defaults to the scenario's")
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--settle-sec", type=float, default=2.0, help="wait after applying the fault")
    parser.add_argument("--dry-run", action="store_true", help="print the commands; touch nothing")
    parser.add_argument("--report", type=Path, default=None, help="write the verdict as JSON")
    args = parser.parse_args(argv)

    scenario = SCENARIOS[args.scenario]
    capability = args.capability or scenario.capability
    arguments = json.loads(args.arguments) if args.arguments else dict(scenario.arguments)

    binary = "tc" if args.dry_run else _require_tc()
    commands = build_commands(scenario.rule, interface=args.interface, binary=binary)

    print(f"scenario:    {scenario.name}")
    print(f"fault:       {scenario.rule.describe()}")
    print(f"expectation: {scenario.expectation}")
    print(f"capability:  {capability}")
    print()

    if args.dry_run:
        print("the run, in order:")
        print("  1. clean baseline dispatch        (WF-C3)")
        print("  2. apply the fault, then probe it (WF-C1)")
        print(render(commands["apply"]))
        print("  3. the faulted dispatch")
        print("  4. clear the fault, then probe it (WF-C2)")
        print(render(commands["clear"]))
        print("  5. a recovery dispatch after the fault is gone (WF-L2)")
        print("  6. audit the ledger              (WF-S1..S6, WF-L1)")
        print()
        print(f"dispatching: {capability} {json.dumps(arguments)}")
        return 0

    from agent_harness.contracts import CapabilityRegistry
    from agent_harness.runtime import ExecutionLedger, ToolClient
    from agent_harness.runtime.ros_backend import RosBackend
    from agent_harness.scripts.ros_tool_executor import RosToolExecutor, SkillRegistry

    skill_registry = SkillRegistry.load(REPO_ROOT / "skills")
    executor = RosToolExecutor(timeout_sec=args.timeout_sec, concurrent=True)
    ledger = ExecutionLedger(args.ledger)
    orphans = ledger.mark_orphans()
    if orphans:
        print(f"# {orphans} orphaned command(s) from a previous run are blocking side effects")
    client = ToolClient(
        RosBackend(executor, skill_registry, concurrent=True),
        CapabilityRegistry.load(REPO_ROOT),
        default_timeout_s=args.timeout_sec,
        max_workers=4,
        ledger=ledger,
    )

    facts = RunFacts(loss_percent=scenario.rule.loss_percent)
    try:
        # 1. baseline: what does the stack do with no fault at all?
        print("1. baseline dispatch (no fault) ...")
        command_id, status, _receipt, elapsed = _dispatch(
            client, capability, arguments, args.timeout_sec
        )
        facts.submitted.append(command_id)
        facts.baseline_status = status
        facts.baseline_seconds = elapsed
        print(f"   baseline: {status} ({elapsed:.2f}s)")

        # 2. apply the fault and confirm it is really there.
        print("\n2. applying the fault ...")
        _run(commands["apply"], dry_run=False)
        time.sleep(args.settle_sec)
        facts.fault_applied = probe_netem(args.interface, binary=binary)
        print(f"   netem observed: {facts.fault_applied}")

        # 3. the faulted dispatch.
        print("\n3. faulted dispatch ...")
        command_id, status, receipt, elapsed = _dispatch(
            client, capability, arguments, args.timeout_sec
        )
        facts.submitted.append(command_id)
        facts.faulted_seconds = elapsed
        if status == "unknown":
            facts.uncertain.add(command_id)
        print(f"   observed: {status} ({elapsed:.2f}s)")
    finally:
        # Before anything else: a fault left on the interface would poison
        # every later experiment on this machine.
        print("\n4. clearing the fault ...")
        _run(commands["clear"], dry_run=False)
        facts.fault_cleared = not probe_netem(args.interface, binary=binary)
        print(f"   netem observed after clear: {facts.fault_cleared}")

        try:
            # 5. recovery: with the link healthy again, does it work?
            print("\n5. recovery dispatch ...")
            command_id, status, _receipt, elapsed = _dispatch(
                client, capability, arguments, args.timeout_sec
            )
            facts.submitted.append(command_id)
            facts.recovery_status = status
            facts.recovery_seconds = elapsed
            print(f"   observed: {status} ({elapsed:.2f}s)")
        except Exception as error:  # noqa: BLE001 - a failed recovery is a failed criterion
            facts.recovery_status = f"dispatch failed: {type(error).__name__}"
            print(f"   recovery dispatch failed: {error}")

        client.shutdown(30.0)
        executor.close()

    view = LedgerView.from_ledger(ledger)
    report = evaluate_wire_fault(view, facts)
    ledger.close()

    print()
    print(report.report())
    print()
    print(f"ledger: {args.ledger}  ({len(view.statuses)} command(s))")
    for command_id, status in view.statuses.items():
        print(f"  {command_id}: {status}")

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "scenario": scenario.name,
                    "fault": scenario.rule.describe(),
                    "expectation": scenario.expectation,
                    "capability": capability,
                    "verdict": report.verdict.value,
                    "criteria": report.to_dict()["results"],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"-> {args.report}")

    if report.verdict is Verdict.VOID:
        return 2
    return 0 if report.verdict is Verdict.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
