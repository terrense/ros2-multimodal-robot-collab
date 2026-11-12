#!/usr/bin/env python3
"""Arrange and clear the world for a calibration probe.

    calibration_scene.py arm visual_occlusion
    calibration_scene.py clear visual_occlusion
    calibration_scene.py list
    calibration_scene.py show low_confidence

Four fault families cannot be produced by calling a capability: occlusion, a
dropped tool, a blocked handover and a mid-motion cancel. This script arranges
the world for the first three and explains what has to happen by hand for the
fourth.

**It never dispatches a robot skill.** It spawns and deletes static props
through the Gazebo services and sets node parameters, so arranging a scene does
not move the robot -- which is the whole reason it is a separate step from
`run_calibration`. A probe that has to move the robot is run by a person who
has said so; putting props in the world is not that.

The scene table and the driver live in `agent_env/calibration_scene.py`. They
are a library rather than part of this script because the ROS installation
ships a `scripts` package of its own, and it shadows this directory for any
import that is not by path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_env.calibration_scene import (  # noqa: E402 - the path fix comes first
    SCENES,
    STATIONS_YAML,
    RosScene,
    load_stations,
    resolve,
    scene_for,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=["arm", "clear", "list", "show"])
    parser.add_argument("family", nargs="?", default=None)
    parser.add_argument("--stations", type=Path, default=STATIONS_YAML)
    args = parser.parse_args(argv)

    if args.action == "list":
        for family, scene in sorted(SCENES.items()):
            state = "props" if scene.props else ("parameter" if scene.parameter else "manual")
            print(f"{family:20s} {state:9s} {scene.note}")
        return 0

    if args.family is None:
        parser.error(f"{args.action} needs a family")

    scene = scene_for(args.family)
    stations = load_stations(args.stations)

    if args.action == "show":
        print(f"[{scene.family}] {scene.note}")
        for prop in scene.props:
            x, y, z = resolve(prop, stations)
            print(f"  {prop.name:22s} {prop.sdf:24s} -> ({x}, {y}, {z})  {prop.role}")
        if scene.parameter is not None:
            node, parameter, value = scene.parameter
            print(f"  parameter {node}.{parameter} = {value}")
        if scene.manual:
            print("  manual steps:")
            for step in scene.manual:
                print(f"    - {step}")
        return 0

    if not scene.props and not scene.parameter:
        print(f"[{scene.family}] {scene.note}")
        for step in scene.manual:
            print(f"  - {step}")
        print("\nNo props to place: this fault is an event, not an arrangement.")
        return 0

    ros = RosScene()
    try:
        if args.action == "arm":
            for prop in scene.props:
                x, y, z = resolve(prop, stations)
                if not ros.spawn(prop.name, prop.path, x, y, z):
                    return 1
            if scene.parameter is not None:
                node, parameter, value = scene.parameter
                ros.set_parameter(node, parameter, value)
            print(f"\n[{scene.family}] armed: {scene.note}")
        else:
            for prop in scene.props:
                ros.delete(prop.name)
            print(f"\n[{scene.family}] cleared")
    finally:
        ros.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
