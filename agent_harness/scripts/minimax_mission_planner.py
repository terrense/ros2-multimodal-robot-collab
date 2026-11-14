#!/usr/bin/env python3
"""MiniMax-only entry point for the mission planner.

The planner itself is `model_mission_planner.py`, which speaks to any
OpenAI-compatible provider. This wrapper keeps the older command working
and pins the provider to MiniMax.

    python3 agent_harness/scripts/minimax_mission_planner.py "deliver the tool"
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_harness.scripts.model_mission_planner import main  # noqa: E402

if __name__ == "__main__":
    sys.argv += ["--provider", "minimax"]
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:  # noqa: BLE001 - show concise CLI errors.
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
