#!/usr/bin/env python3
"""Generate the workshop world for the collaboration lab.

All decoration is geometry, not textures: Gazebo Classic's scene init failed
with Ogre material scripts, and boxes are deterministic. The east wall (with
the doorway) is a checker wall carrying "laboratory" and "factory" in
gradient-coloured block letters; the west wall carries a calibration target.

Run: .venv/Scripts/python scripts/generate_workshop_world.py
"""

from __future__ import annotations

import colorsys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "robot_collab_sim" / "worlds" / "workshop.world"

HEADER = """<?xml version="1.0"?>
<sdf version="1.6">
  <world name="collab_workshop">
    <physics name="default_physics" default="0" type="ode">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>1000</real_time_update_rate>
    </physics>

    <plugin name="gazebo_ros_state" filename="libgazebo_ros_state.so">
      <ros><namespace>/</namespace></ros>
      <update_rate>30.0</update_rate>
    </plugin>

    <include><uri>model://ground_plane</uri></include>
    <include><uri>model://sun</uri></include>
"""

FOOTER = """  </world>
</sdf>
"""

# 3x5 block glyphs, enough for "laboratory" and "factory".
FONT = {
    "a": ("111", "101", "111", "101", "101"),
    "b": ("110", "101", "111", "101", "110"),
    "c": ("111", "100", "100", "100", "111"),
    "f": ("111", "100", "110", "100", "100"),
    "l": ("100", "100", "100", "100", "111"),
    "o": ("111", "101", "101", "101", "111"),
    "r": ("110", "101", "110", "100", "100"),
    "t": ("111", "010", "010", "010", "010"),
    "y": ("101", "101", "111", "001", "111"),
}


def material(rgb: tuple[float, float, float]) -> str:
    r, g, b = rgb
    return (
        "<material>"
        f"<ambient>{r} {g} {b} 1</ambient>"
        f"<diffuse>{r} {g} {b} 1</diffuse>"
        "</material>"
    )


def box_model(
    name: str,
    pose: tuple[float, float, float, float],
    size: tuple[float, float, float],
    rgb: tuple[float, float, float],
    *,
    collide: bool = True,
) -> str:
    x, y, z, yaw = pose
    sx, sy, sz = size
    collision = (
        f"""        <collision name="collision"><geometry><box><size>{sx} {sy} {sz}</size></box></geometry></collision>
"""
        if collide
        else ""
    )
    return f"""    <model name="{name}">
      <pose>{x} {y} {z} 0 0 {yaw}</pose>
      <static>true</static>
      <link name="link">
        <visual name="visual">
          <geometry><box><size>{sx} {sy} {sz}</size></box></geometry>
          {material(rgb)}
        </visual>
{collision}      </link>
    </model>
"""


def cylinder_model(
    name: str,
    pose: tuple[float, float, float, float],
    radius: float,
    length: float,
    rgb: tuple[float, float, float],
) -> str:
    x, y, z, yaw = pose
    return f"""    <model name="{name}">
      <pose>{x} {y} {z} 0 0 {yaw}</pose>
      <static>true</static>
      <link name="link">
        <visual name="visual">
          <geometry><cylinder><radius>{radius}</radius><length>{length}</length></cylinder></geometry>
          {material(rgb)}
        </visual>
      </link>
    </model>
"""


def hsv_color(index: int, total: int) -> tuple[float, float, float]:
    hue = index / max(1, total - 1)
    r, g, b = colorsys.hsv_to_rgb(hue, 0.95, 0.98)
    return round(r, 3), round(g, 3), round(b, 3)


def wall_text(
    tag: str,
    text: str,
    wall_x: float,
    center_y: float,
    center_z: float,
    cell: float,
    reverse: bool = False,
) -> list[str]:
    """Block letters on a wall. ``reverse`` reads them from inside the room."""
    width = len(text) * 3 * cell
    start_y = center_y + width / 2 if reverse else center_y - width / 2
    parts: list[str] = []
    for letter_index, char in enumerate(text):
        glyph = FONT.get(char)
        if glyph is None:
            continue
        color = hsv_color(letter_index, len(text))
        for row, line in enumerate(glyph):
            for col, bit in enumerate(line):
                if bit != "1":
                    continue
                offset = letter_index * 3 * cell + col * cell + cell / 2
                y = start_y - offset if reverse else start_y + offset
                z = center_z + (2 - row) * cell + cell / 2
                parts.append(
                    box_model(
                        f"{tag}_letter_{letter_index}_{row}_{col}",
                        (wall_x, y, z, 0.0),
                        (0.02, cell, cell),
                        color,
                        collide=False,
                    )
                )
    return parts


def checker_tiles(
    tag: str,
    wall_x: float,
    center_y: float,
    width: float,
    center_z: float,
    height: float,
    cell: float,
) -> list[str]:
    """Black squares of a checkerboard, applied on top of a light wall."""
    parts: list[str] = []
    cols = int(width / cell)
    rows = int(height / cell)
    start_y = center_y - cols * cell / 2
    start_z = center_z - rows * cell / 2
    for row in range(rows):
        for col in range(cols):
            if (row + col) % 2 == 0:
                continue
            parts.append(
                box_model(
                    f"{tag}_tile_{row}_{col}",
                    (wall_x, start_y + col * cell + cell / 2, start_z + row * cell + cell / 2, 0.0),
                    (0.02, cell, cell),
                    (0.05, 0.05, 0.05),
                    collide=False,
                )
            )
    return parts


def arm_cell(
    name: str,
    table_color: tuple[float, float, float],
    table_pose: tuple[float, float, float, float],
) -> str:
    """The cell table. The arm itself is spawned from its official URDF."""
    tx, ty, table_z, _yaw = table_pose
    parts = [
        f"    <!-- {name}: table for an arm spawned from its official URDF -->\n",
        box_model(f"{name}_table", table_pose, (2.4, 1.4, 0.06), table_color, collide=True),
    ]
    for index, (dx, dy) in enumerate([(-1.1, -0.6), (-1.1, 0.6), (1.1, -0.6), (1.1, 0.6)]):
        parts.append(
            box_model(
                f"{name}_leg_{index}",
                (tx + dx, ty + dy, table_z / 2.0, 0.0),
                (0.08, 0.08, table_z),
                (0.25, 0.20, 0.15),
            )
        )
    return "".join(parts)


def wall_camera() -> str:
    """Wall-mounted overview camera, looking into the room along +y."""
    return """    <model name="workshop_overhead_camera">
      <pose>0 -3.85 2.60 0 0.35 1.5708</pose>
      <static>true</static>
      <link name="camera_link">
        <visual name="body">
          <geometry><box><size>0.12 0.10 0.10</size></box></geometry>
          <material><ambient>0.1 0.1 0.1 1</ambient><diffuse>0.1 0.1 0.1 1</diffuse></material>
        </visual>
        <sensor name="overhead_camera" type="camera">
          <always_on>true</always_on>
          <visualize>false</visualize>
          <update_rate>10</update_rate>
          <camera>
            <horizontal_fov>1.9</horizontal_fov>
            <image><width>1280</width><height>720</height><format>R8G8B8</format></image>
            <clip><near>0.1</near><far>30.0</far></clip>
          </camera>
          <plugin name="overhead_camera_controller" filename="libgazebo_ros_camera.so">
            <ros><namespace>/</namespace></ros>
            <camera_name>workshop/overhead</camera_name>
            <frame_name>overhead_camera_link</frame_name>
          </plugin>
        </sensor>
      </link>
    </model>
"""


def build() -> str:
    parts = [HEADER]
    # Four walls, no ceiling. The east wall has a 3 m doorway.
    parts.append(box_model("wall_north", (0, 4.1, 1.5, 0), (10.4, 0.2, 3.0), (0.72, 0.72, 0.75)))
    parts.append(box_model("wall_south", (0, -4.1, 1.5, 0), (10.4, 0.2, 3.0), (0.72, 0.72, 0.75)))
    parts.append(box_model("wall_west", (-5.1, 0, 1.5, 0), (0.2, 8.4, 3.0), (0.72, 0.72, 0.75)))
    parts.append(box_model("wall_east_a", (5.1, -2.85, 1.5, 0), (0.2, 2.7, 3.0), (0.88, 0.88, 0.9)))
    parts.append(box_model("wall_east_b", (5.1, 2.85, 1.5, 0), (0.2, 2.7, 3.0), (0.88, 0.88, 0.9)))
    parts.append(box_model("wall_east_lintel", (5.1, 0, 2.7, 0), (0.2, 3.0, 0.6), (0.66, 0.66, 0.69)))

    # Checker wall + gradient logos either side of the doorway.
    parts.extend(checker_tiles("east_a", 4.98, -2.85, 2.7, 1.5, 3.0, 0.45))
    parts.extend(checker_tiles("east_b", 4.98, 2.85, 2.7, 1.5, 3.0, 0.45))
    parts.extend(wall_text("lab", "laboratory", 4.97, -2.85, 1.55, 0.09, reverse=True))
    parts.extend(wall_text("fac", "factory", 4.97, 2.85, 1.55, 0.12, reverse=True))

    # Calibration target on the opposite wall: 9x6 squares.
    parts.extend(checker_tiles("calib", -4.98, 0.0, 3.6, 1.5, 2.4, 0.4))

    # Two robot cells: brown and beige tables.
    parts.append(arm_cell("kuka_kr10", (0.38, 0.22, 0.10), (-2.6, -1.8, 0.375, 0.0)))
    parts.append(arm_cell("fanuc_m10", (0.85, 0.76, 0.60), (-2.6, 1.8, 0.375, 0.0)))

    # Tool bench with a toolbox, wrench, hammer and screwdriver.
    parts.append(box_model("tool_bench_top", (2.6, 2.4, 0.375, 0.0), (1.6, 1.0, 0.06), (0.42, 0.28, 0.16)))
    for i, (dx, dy) in enumerate([(-0.7, -0.4), (-0.7, 0.4), (0.7, -0.4), (0.7, 0.4)]):
        parts.append(
            box_model(f"tool_bench_leg_{i}", (2.6 + dx, 2.4 + dy, 0.18, 0.0), (0.07, 0.07, 0.36), (0.3, 0.2, 0.1))
        )
    parts.append(box_model("toolbox", (2.35, 2.25, 0.47, 0.2), (0.36, 0.2, 0.14), (0.75, 0.1, 0.1)))
    parts.append(box_model("toolbox_lid", (2.35, 2.25, 0.55, 0.2), (0.36, 0.2, 0.02), (0.6, 0.08, 0.08)))
    parts.append(box_model("wrench_handle", (2.75, 2.3, 0.42, 0.4), (0.02, 0.16, 0.012), (0.7, 0.7, 0.72)))
    parts.append(box_model("wrench_jaw_a", (2.82, 2.25, 0.42, 0.4), (0.02, 0.03, 0.02), (0.7, 0.7, 0.72)))
    parts.append(box_model("wrench_jaw_b", (2.82, 2.36, 0.42, 0.4), (0.02, 0.03, 0.02), (0.7, 0.7, 0.72)))
    parts.append(cylinder_model("hammer_handle", (2.05, 2.38, 0.45, 0.5), 0.012, 0.26, (0.55, 0.35, 0.15)))
    parts.append(box_model("hammer_head", (2.12, 2.42, 0.45, 0.5), (0.08, 0.04, 0.04), (0.3, 0.3, 0.32)))
    parts.append(cylinder_model("screwdriver_handle", (2.15, 2.25, 0.44, 1.1), 0.015, 0.09, (0.9, 0.75, 0.1)))
    parts.append(cylinder_model("screwdriver_shaft", (2.15, 2.31, 0.44, 1.1), 0.004, 0.13, (0.75, 0.75, 0.78)))

    parts.append(wall_camera())
    parts.append(FOOTER)
    return "".join(parts)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text.splitlines())} lines)")
