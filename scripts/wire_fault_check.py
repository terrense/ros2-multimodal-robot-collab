#!/usr/bin/env python3
"""Inject a transport fault, run one command, and audit the ledger.

    sudo python3 scripts/wire_fault_check.py dropped_result --ledger /tmp/wire.db
    python3 scripts/wire_fault_check.py dropped_result --dry-run

The in-process faults prove the state machine. This proves the transport: real
packet loss under a real DDS stack, with the robot's motion at stake.

Three rules, and they are the whole point:

1. **The interface is restored in a `finally`.** A crash must not leave a
   netem rule on the machine — that would make every later experiment wrong in
   a way nobody attributes to this tool.
2. **Nothing is dispatched before the fault is in place.** A run that measures
   the clean path and reports it as the faulted one is worse than no run.
3. **The verdict comes from the ledger, not from this script.** Whether a
   command was dispatched twice is a fact about the ledger; the harness only
   reads it.

Requires root for `tc`, a running stack, and a person: the command moves the
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
    build_commands,
    check_ledger,
    render,
)


def _run(commands: list[list[str]], *, dry_run: bool) -> int:
    for command in commands:
        print("  $ " + " ".join(command))
        if dry_run:
            continue
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        # `tc qdisc del` fails when there is nothing to delete; that is fine.
        if result.returncode != 0 and "Cannot delete" not in (result.stderr or ""):
            print(f"    (exit {result.returncode}) {result.stderr.strip()}")
    return 0


def _require_tc() -> str:
    binary = shutil.which("tc")
    if binary is None:
        raise SystemExit(
            "`tc` is not installed. Install iproute2:\n"
            "  sudo apt install iproute2"
        )
    return binary


def _apply(commands: dict[str, list[list[str]]], *, dry_run: bool) -> None:
    print("apply:")
    _run(commands["apply"], dry_run=dry_run)


def _clear(commands: dict[str, list[list[str]]], *, dry_run: bool) -> None:
    print("clear:")
    _run(commands["clear"], dry_run=dry_run)


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
        print("would run:")
        print(render(commands["apply"]))
        print()
        print("then dispatch:")
        print(f"  {capability} {json.dumps(arguments)}")
        print()
        print("then always:")
        print(render(commands["clear"]))
        print()
        print("then audit the ledger and report:")
        print("  no_duplicate_effect / uncertain_stays_unknown / blocked_until_ack")
        return 0

    if not capability:
        raise SystemExit("no capability to dispatch")

    from agent_harness.contracts import ActionRequest, CapabilityRegistry, new_command_id
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

    request = ActionRequest(
        episode_id="wire-fault",
        command_id=new_command_id(),
        capability=capability,
        arguments=arguments,
        timeout_s=args.timeout_sec,
    )

    receipt = None
    try:
        _apply(commands, dry_run=False)
        time.sleep(args.settle_sec)

        print(f"\ndispatch {capability} under fault ...")
        submitted = client.submit(request)
        status = str(getattr(submitted.execution_status, "value", submitted.execution_status))
        receipt = client.wait(request.command_id) if status == "queued" else submitted
        observed = str(getattr(receipt.execution_status, "value", receipt.execution_status))
        print(f"  observed: {observed}  {getattr(receipt, 'failure_code', '') or ''}")
    finally:
        # Before anything else: a fault left on the interface would poison
        # every later experiment on this machine.
        print()
        _clear(commands, dry_run=False)
        client.shutdown(30.0)
        executor.close()

    # A timeout is the case the audit exists for: the effect may have happened.
    uncertain = {request.command_id} if receipt is None or str(
        getattr(receipt.execution_status, "value", receipt.execution_status)
    ) == "unknown" else set()

    view = LedgerView.from_ledger(ledger)
    verdict = check_ledger(view, uncertain_expected=uncertain)
    ledger.close()

    print()
    print(verdict.report())
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
                    "observed_status": str(
                        getattr(receipt.execution_status, "value", receipt.execution_status)
                    ),
                    "survived": verdict.survived,
                    "no_duplicate_effect": verdict.no_duplicate_effect,
                    "uncertain_stays_unknown": verdict.uncertain_stays_unknown,
                    "blocked_until_ack": verdict.blocked_until_ack,
                    "failures": verdict.failures,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"-> {args.report}")

    return 0 if verdict.survived else 1


if __name__ == "__main__":
    sys.exit(main())
