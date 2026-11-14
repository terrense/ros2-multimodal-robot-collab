#!/usr/bin/env python3
"""Generate mission plans with a hosted model and route them through the harness.

    python -m agent_harness.scripts.model_mission_planner "take the hex key to station A"
    python -m agent_harness.scripts.model_mission_planner "stop the arm" --provider minimax
    python -m agent_harness.scripts.model_mission_planner "deliver the tool" --dry-run

The model is only responsible for producing structured mission-plan JSON. The
local harness still validates every skill/action and is the only path to ROS2
side effects: a plan that names a skill this robot does not have is refused
before anything moves.

The API key is read from the environment (`DEEPSEEK_API_KEY` or
`MINIMAX_API_KEY`), or from a local `.env` file. It is never kept in the
repository, which is public.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_harness.agent.hosted_model import chat_json, resolve_provider  # noqa: E402
from agent_harness.scripts.ros_tool_executor import (  # noqa: E402
    DEFAULT_SKILLS_DIR,
    SkillRegistry,
    dry_run_plan,
)

SYSTEM_PROMPT = """You are MissionSupervisor for a ROS2 robot collaboration system.

Return exactly one JSON object matching this mission plan shape:
{
  "intent": "deliver_tool | pause_arm | resume_arm | emergency_stop | query_state",
  "operator_id": "operator_001",
  "tool_id": "optional tool id",
  "target_station": "optional station id",
  "safety": {
    "requires_face_auth": true,
    "requires_handover_confirmation": true,
    "stop_on_open_palm": true
  },
  "steps": [
    {
      "skill": "skill name",
      "action": "action name",
      "args": {}
    }
  ]
}

Allowed callable skill actions:
- perception.verify_operator(args: operator_id, require_face_match)
- mission-control.deliver_tool(args: tool_id, target_station, operator_id, require_confirmation)
- navigation.navigate_to_station(args: station_id, reason)
- manipulation.publish_arm_control(args: command, operator_id, emergency, detail)
- hri.publish_tts(args: text)
- system-state.query_state(args: requester)

Valid stations: tool_shelf, station_a, station_b, handover_zone.
Valid tool ids: hex_key_3mm. The stack publishes exactly this id, and a
mission that asks for a different one fails with "could not find tool".
Valid manipulation commands: arm_start, arm_pause, system_stop.
Do not output markdown. Do not call sensor output skills such as YOLO or OpenPose.
Prefer the mission-control.deliver_tool action for end-to-end delivery requests.
For emergency stop, publish manipulation.publish_arm_control with command system_stop and emergency true.
"""


def load_env_file(path: Path) -> None:
    """Read a local .env file into the environment.

    The file is local and git-ignored: a key that is in the repository is a
    key that is lost.
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def build_plan(
    prompt: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    temperature: float = 0.1,
    timeout_sec: float = 60.0,
) -> tuple[dict[str, Any], Any]:
    """Ask the model for a plan, and return the plan and the provider used."""
    chosen = resolve_provider(provider)
    if model:
        chosen = type(chosen)(chosen.name, base_url or chosen.base_url, model, chosen.key_env)
    if base_url:
        chosen = type(chosen)(chosen.name, base_url, chosen.model, chosen.key_env)
    plan = chat_json(
        prompt,
        system=SYSTEM_PROMPT,
        provider=chosen,
        temperature=temperature,
        timeout_sec=timeout_sec,
    )
    return plan, chosen


def print_plan(plan: dict[str, Any], output_path: Path | None) -> None:
    text = json.dumps(plan, indent=2, ensure_ascii=False)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
        print(f"# wrote plan: {output_path}")
    else:
        print(text)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Use a hosted model to generate a validated ROS2 mission plan."
    )
    parser.add_argument("prompt", help="Natural language mission request.")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
    parser.add_argument(
        "--provider",
        default=None,
        help="deepseek or minimax. The first provider with a key configured is used by default.",
    )
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--no-response-format",
        action="store_true",
        help="Do not send OpenAI-compatible response_format; rely on prompt-only JSON output.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Also print harness dry-run dispatch results."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print dry-run results as JSON when --dry-run is used."
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    plan, provider = build_plan(
        args.prompt,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        timeout_sec=args.timeout_sec,
    )
    print(f"# plan from {provider.name} ({provider.model})", file=sys.stderr)

    registry = SkillRegistry.load(args.skills_dir)
    calls = registry.validate_plan(plan)
    print_plan(plan, args.output)

    if args.dry_run:
        results = dry_run_plan(plan, calls, registry)
        if args.json:
            print(json.dumps([asdict(item) for item in results], indent=2, ensure_ascii=False))
        else:
            for item in results:
                print(f"\n# step {item.step_index}: {item.skill}.{item.action}")
                print(f"# risk: {item.result.get('risk')}")
                print(item.command)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001 - show concise CLI errors.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
