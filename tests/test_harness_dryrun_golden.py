"""Freeze the dry-run CLI output.

The Phase 2 runtime refactor must not change a single byte of what
``ros_tool_executor.py`` prints in dry-run mode. These tests are the anchor:
if they fail, the change altered observable behaviour and is no longer a
behaviour-preserving refactor.

The only normalisation is the generated trace id. Neither example plan pins
one, so the harness generates ``trace-<10 hex>``; everything else is compared
verbatim.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "agent_harness" / "scripts" / "ros_tool_executor.py"
EXAMPLES = REPO_ROOT / "agent_harness" / "examples"
GOLDEN = REPO_ROOT / "agent_harness" / "tests" / "golden"

_GENERATED_TRACE = re.compile(r"trace-[0-9a-f]{10}")


def _run_dry_run(plan_name: str, *, as_json: bool) -> str:
    command = [
        sys.executable,
        str(SCRIPT),
        str(EXAMPLES / f"{plan_name}.json"),
        "--skills-dir",
        str(REPO_ROOT / "skills"),
    ]
    if as_json:
        command.append("--json")
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return _GENERATED_TRACE.sub("trace-NORMALIZED", completed.stdout)


@pytest.mark.parametrize("plan_name", ["deliver_hex_key_plan", "emergency_stop_plan"])
@pytest.mark.parametrize(("as_json", "suffix"), [(True, "json"), (False, "human")])
def test_dry_run_output_is_unchanged(plan_name: str, as_json: bool, suffix: str) -> None:
    expected = (GOLDEN / f"{plan_name}.{suffix}").read_text(encoding="utf-8")
    assert _run_dry_run(plan_name, as_json=as_json) == expected
