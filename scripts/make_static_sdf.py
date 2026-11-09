#!/usr/bin/env python3
"""Convert a URDF to a static SDF model.

MoveIt descriptions are planning models: they have joints but no controllers.
Spawned as dynamic they simply fall over under gravity. Static SDF keeps them
upright in the scene while the agent/perception layers reason about them.

Usage: make_static_sdf.py IN.urdf OUT.sdf
"""

from __future__ import annotations

import re
import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: make_static_sdf.py IN.urdf OUT.sdf", file=sys.stderr)
        return 2
    source, target = argv
    sdf = subprocess.run(
        ["gz", "sdf", "-p", source], capture_output=True, text=True, check=True
    ).stdout
    sdf, count = re.subn(
        r"(<model\b[^>]*>)", r"\1\n    <static>true</static>", sdf, count=1
    )
    if count == 0:
        print("no <model> element found in SDF", file=sys.stderr)
        return 1
    open(target, "w", encoding="utf-8").write(sdf)
    print(f"{target}: static model written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
