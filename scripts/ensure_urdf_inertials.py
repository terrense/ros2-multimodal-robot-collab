#!/usr/bin/env python3
"""Give every URDF link a default inertial if it has none.

MoveIt planning URDFs often omit <inertial>; Gazebo's URDF→SDF conversion then
drops the links entirely and the robot renders as nothing. This adds a minimal
inertial so the model spawns visibly.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_MASS = "1.0"
DEFAULT_I = "0.01"


def ensure(path: Path) -> int:
    tree = ET.parse(path)
    root = tree.getroot()
    added = 0
    for link in root.findall("link"):
        if link.find("inertial") is not None:
            continue
        inertial = ET.Element("inertial")
        ET.SubElement(inertial, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
        ET.SubElement(inertial, "mass", {"value": DEFAULT_MASS})
        ET.SubElement(
            inertial,
            "inertia",
            {
                "ixx": DEFAULT_I,
                "ixy": "0.0",
                "ixz": "0.0",
                "iyy": DEFAULT_I,
                "iyz": "0.0",
                "izz": DEFAULT_I,
            },
        )
        link.insert(0, inertial)
        added += 1
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return added


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: ensure_urdf_inertials.py FILE.urdf [...]", file=sys.stderr)
        return 2
    for name in argv:
        path = Path(name)
        count = ensure(path)
        print(f"{path}: added {count} inertial block(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
