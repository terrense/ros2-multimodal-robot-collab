#!/usr/bin/env python3
"""Arrange and clear the world for a calibration probe.

    calibration_scene.py arm visual_occlusion
    calibration_scene.py clear visual_occlusion
    calibration_scene.py list

Four fault families cannot be produced by calling a capability: occlusion, a
dropped tool, a blocked handover and a mid-motion cancel. This script arranges
the world for the first three and explains what has to happen by hand for the
fourth.

**It never dispatches a robot skill.** It spawns and deletes static props
through the Gazebo services, so arranging a scene does not move the robot --
which is the whole reason it is a separate step from `run_calibration`. A probe
that has to move the robot is run by a person who has said so; putting props in
the world is not that.

Props are spawned at fixed offsets from a named station rather than at
hard-coded world coordinates, so a scene stays attached to a station if the
station registry changes. The offsets are in `SCENES` below and are the only
numbers that need touching when the layout moves.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENE_DIR = REPO_ROOT / "src" / "robot_collab_sim" / "models" / "calibration"
STATIONS_YAML = REPO_ROOT / "src" / "robot_collab_bringup" / "config" / "stations.yaml"

ROBOT_START = (0.0, 0.0)


@dataclass(frozen=True)
class Prop:
    """One static prop, positioned relative to a station."""

    name: str
    sdf: str
    station: str
    #: Offset from the station, in metres.
    offset: tuple[float, float]
    #: Height above the ground plane.
    z: float
    role: str

    @property
    def path(self) -> Path:
        return SCENE_DIR / self.sdf


@dataclass(frozen=True)
class Scene:
    family: str
    props: tuple[Prop, ...]
    note: str
    #: What a human has to do, when props are not enough.
    manual: tuple[str, ...] = field(default_factory=tuple)


SCENES: dict[str, Scene] = {
    "visual_occlusion": Scene(
        family="visual_occlusion",
        props=(
            Prop(
                name="calib_occluder",
                sdf="calib_occluder.sdf",
                station="tool_shelf",
                offset=(-0.55, 0.10),
                z=0.0,
                role="blocks the D435's view of the shelf from the start pose",
            ),
        ),
        note="the base starts where the shelf is visible but the tool is not",
    ),
    "tool_drop": Scene(
        family="tool_drop",
        props=(
            Prop(
                name="calib_dropped_tool",
                sdf="calib_dropped_tool.sdf",
                station="handover_zone",
                offset=(-0.85, -0.55),
                z=0.0,
                role="the tool on the floor along the delivery route, within arm reach",
            ),
        ),
        note="the tool is on the floor where a second pick can reach it",
    ),
    "no_progress": Scene(
        family="no_progress",
        props=(
            Prop(
                name="calib_station_blocker",
                sdf="calib_station_blocker.sdf",
                station="handover_zone",
                offset=(0.0, 0.0),
                z=0.0,
                role="occupies the handover station so a handover there is refused",
            ),
        ),
        note="the handover station is occupied until the operator is called over",
    ),
    "user_cancel": Scene(
        family="user_cancel",
        props=(),
        note="no props: the fault is an event, not an arrangement",
        manual=(
            "start a delivery mission",
            "wait until the base is in motion",
            "publish a cancel on /hri/gesture_command with command=system_stop, "
            "or cancel the mission goal directly",
            "record whether the motion stopped and the mission reported cancelled",
        ),
    ),
}


def load_stations(path: Path = STATIONS_YAML) -> dict[str, tuple[float, float]]:
    """Station name -> (x, y) from the bringup registry."""
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stations: dict[str, tuple[float, float]] = {}
    for name, entry in (data.get("stations") or {}).items():
        pose = (entry or {}).get("pose") or {}
        stations[str(name)] = (float(pose.get("x", 0.0)), float(pose.get("y", 0.0)))
    return stations


def resolve(prop: Prop, stations: dict[str, tuple[float, float]]) -> tuple[float, float, float]:
    """Where to spawn a prop: the station's pose plus its offset."""
    if prop.station not in stations:
        raise SystemExit(
            f"{prop.name} is positioned relative to {prop.station!r}, "
            f"which is not in {STATIONS_YAML}"
        )
    x, y = stations[prop.station]
    return (round(x + prop.offset[0], 3), round(y + prop.offset[1], 3), prop.z)


def scene_for(family: str) -> Scene:
    scene = SCENES.get(family)
    if scene is None:
        known = ", ".join(sorted(SCENES))
        raise SystemExit(f"unknown scene {family!r}; known: {known}")
    return scene


# ------------------------------------------------------------------ gazebo


class _Gazebo:
    """Spawn and delete entities through the Gazebo services."""

    def __init__(self) -> None:
        import rclpy
        from gazebo_msgs.srv import DeleteEntity, SpawnEntity

        self.rclpy = rclpy
        self.DeleteEntity = DeleteEntity
        self.SpawnEntity = SpawnEntity
        self.rclpy.init()
        self.node = self.rclpy.create_node("calibration_scene")

    def close(self) -> None:
        self.node.destroy_node()
        self.rclpy.shutdown()

    def _service(self, fragment: str, timeout: float = 10.0):
        end = time.time() + timeout
        while time.time() < end:
            names = [name for name, _ in self.node.get_service_names_and_types() if fragment in name]
            if names:
                return names[0]
            self.rclpy.spin_once(self.node, timeout_sec=0.2)
        return None

    def spawn(self, name: str, path: Path, x: float, y: float, z: float) -> bool:
        from geometry_msgs.msg import Pose

        service = self._service("spawn_entity")
        if service is None:
            raise SystemExit("spawn_entity is unavailable: is Gazebo running?")
        client = self.node.create_client(self.SpawnEntity, service)
        client.wait_for_service(timeout_sec=5.0)
        request = self.SpawnEntity.Request()
        request.name = name
        request.xml = path.read_text(encoding="utf-8")
        request.initial_pose = Pose()
        request.initial_pose.position.x = x
        request.initial_pose.position.y = y
        request.initial_pose.position.z = z
        future = client.call_async(request)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=30.0)
        result = future.result()
        ok = bool(result and result.success)
        print(f"spawn {name} at ({x}, {y}, {z}): {'ok' if ok else (result.status_message if result else 'timeout')}")
        return ok

    def delete(self, name: str) -> bool:
        service = self._service("delete_entity")
        if service is None:
            raise SystemExit("delete_entity is unavailable: is Gazebo running?")
        client = self.node.create_client(self.DeleteEntity, service)
        client.wait_for_service(timeout_sec=5.0)
        request = self.DeleteEntity.Request()
        request.name = name
        future = client.call_async(request)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=20.0)
        result = future.result()
        ok = bool(result and result.success)
        print(f"delete {name}: {'ok' if ok else 'not present'}")
        return ok


# --------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["arm", "clear", "list", "show"])
    parser.add_argument("family", nargs="?", default=None)
    parser.add_argument("--stations", type=Path, default=STATIONS_YAML)
    args = parser.parse_args(argv)

    if args.action == "list":
        for family, scene in sorted(SCENES.items()):
            state = "props" if scene.props else "manual"
            print(f"{family:20s} {state:6s} {scene.note}")
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
        if scene.manual:
            print("  manual steps:")
            for step in scene.manual:
                print(f"    - {step}")
        return 0

    if not scene.props:
        print(f"[{scene.family}] {scene.note}")
        for step in scene.manual:
            print(f"  - {step}")
        print("\nNo props to place: this fault is an event, not an arrangement.")
        return 0

    gazebo = _Gazebo()
    try:
        if args.action == "arm":
            for prop in scene.props:
                x, y, z = resolve(prop, stations)
                if not gazebo.spawn(prop.name, prop.path, x, y, z):
                    return 1
            print(f"\n[{scene.family}] armed: {scene.note}")
        else:
            for prop in scene.props:
                gazebo.delete(prop.name)
            print(f"\n[{scene.family}] cleared")
    finally:
        gazebo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
