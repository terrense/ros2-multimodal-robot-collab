#!/usr/bin/env python3
"""Generate mission plans with MiniMax M3 and route them through the harness.

The model is only responsible for producing structured mission-plan JSON. The
local harness still validates every skill/action and is the only path to ROS2
side effects.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ros_tool_executor import DEFAULT_SKILLS_DIR, SkillRegistry, dry_run_plan


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_BASE_URL = "https://api.minimax.chat/v1"


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
Valid manipulation commands: arm_start, arm_pause, system_stop.
Do not output markdown. Do not call sensor output skills such as YOLO or OpenPose.
Prefer the mission-control.deliver_tool action for end-to-end delivery requests.
For emergency stop, publish manipulation.publish_arm_control with command system_stop and emergency true.
"""


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def request_minimax_plan(
    prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    timeout_sec: float,
    response_format: bool,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if response_format:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"MiniMax API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MiniMax API request failed: {exc}") from exc

    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected MiniMax response shape: {json.dumps(data, ensure_ascii=False)[:800]}") from exc


def parse_plan_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def print_plan(plan: dict[str, Any], output_path: Path | None) -> None:
    text = json.dumps(plan, indent=2, ensure_ascii=False)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
        print(f"# wrote plan: {output_path}")
    else:
        print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="Use MiniMax M3 to generate a validated ROS2 mission plan.")
    parser.add_argument("prompt", help="Natural language mission request.")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    parser.add_argument("--skills-dir", type=Path, default=DEFAULT_SKILLS_DIR)
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
    parser.add_argument("--dry-run", action="store_true", help="Also print harness dry-run dispatch results.")
    parser.add_argument("--json", action="store_true", help="Print dry-run results as JSON when --dry-run is used.")
    args = parser.parse_args()

    load_env_file(args.env_file)
    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        raise SystemExit("MINIMAX_API_KEY is required. Export it or put it in a local .env file.")
    model = args.model or os.getenv("MINIMAX_MODEL", DEFAULT_MODEL)
    base_url = args.base_url or os.getenv("MINIMAX_BASE_URL", DEFAULT_BASE_URL)

    plan_text = request_minimax_plan(
        args.prompt,
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=args.temperature,
        timeout_sec=args.timeout_sec,
        response_format=not args.no_response_format,
    )
    plan = parse_plan_text(plan_text)

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
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001 - show concise CLI errors.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
