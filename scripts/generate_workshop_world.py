#!/usr/bin/env python3
"""Generate the workshop world for the collaboration lab.

The KUKA / FANUC / DOBOT cells are representative articulated geometry, not
manufacturer CAD: they carry the right names, colours, footprint and mounting
height so the perception/agent layers can reason about them, and so real URDFs
can replace them later without moving anything else.

Output: src/robot_collab_sim/worlds/workshop.world
Run:    .venv/Scripts/python scripts/generate_workshop_world.py
"""

from __future__ import annotations

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


def arm_cell(
    name: str,
    origin: tuple[float, float],
    rgb: tuple[float, float, float],
    table_pose: tuple[float, float, float, float],
) -> str:
    """A six-link arm silhouette mounted on a table."""
    tx, ty, table_z, yaw = table_pose
    x, y = origin
    parts = [
        f"    <!-- {name}: representative six-axis arm on its table -->\n",
        box_model(f"{name}_table", table_pose, (1.2, 0.8, 0.05), (0.35, 0.35, 0.38), collide=True),
        # table legs
    ]
    # four legs under the table top
    legs = [(-0.5, -0.3), (-0.5, 0.3), (0.5, -0.3), (0.5, 0.3)]
    for i, (dx, dy) in enumerate(legs):
        parts.append(
            box_model(
                f"{name}_leg_{i}",
                (tx + dx, ty + dy, table_z / 2.0, 0.0),
                (0.06, 0.06, table_z),
                (0.25, 0.25, 0.27),
            )
        )
    base_z = table_z + 0.05 + 0.10
    parts.append(
        cylinder_model(f"{name}_base", (x, y, base_z, 0.0), 0.16, 0.20, rgb)
    )
    # link chain: leaning boxes/cylinders, representative silhouette
    parts.append(
        cylinder_model(f"{name}_link1", (x, y, base_z + 0.32, 0.0), 0.11, 0.44, rgb)
    )
    parts.append(
        box_model(f"{name}_link2", (x + 0.22, y, base_z + 0.56, 0.35), (0.52, 0.14, 0.14), rgb)
    )
    parts.append(
        box_model(f"{name}_link3", (x + 0.48, y, base_z + 0.40, -0.55), (0.42, 0.11, 0.11), rgb)
    )
    parts.append(
        box_model(f"{name}_wrist", (x + 0.66, y, base_z + 0.22, 0.0), (0.10, 0.10, 0.18), (0.15, 0.15, 0.15))
    )
    parts.append(
        box_model(
            f"{name}_tool_flange", (x + 0.66, y, base_z + 0.10, 0.0), (0.08, 0.08, 0.06), (0.6, 0.6, 0.62)
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
    # No ceiling: four walls only.
    parts.append(box_model("wall_north", (0, 4.1, 1.5, 0), (10.4, 0.2, 3.0), (0.72, 0.72, 0.75)))
    parts.append(box_model("wall_south", (0, -4.1, 1.5, 0), (10.4, 0.2, 3.0), (0.72, 0.72, 0.75)))
    # East wall is open: a 3 m doorway with a lintel, so the cell is enterable.
    parts.append(box_model("wall_east_a", (5.1, -2.85, 1.5, 0), (0.2, 2.7, 3.0), (0.72, 0.72, 0.75)))
    parts.append(box_model("wall_east_b", (5.1, 2.85, 1.5, 0), (0.2, 2.7, 3.0), (0.72, 0.72, 0.75)))
    parts.append(box_model("wall_east_lintel", (5.1, 0, 2.7, 0), (0.2, 3.0, 0.6), (0.66, 0.66, 0.69)))
    parts.append(box_model("wall_west", (-5.1, 0, 1.5, 0), (0.2, 8.4, 3.0), (0.72, 0.72, 0.75)))

    # Two robot cells: KUKA orange, FANUC yellow.
    parts.append(arm_cell("kuka_kr10", (-2.6, -1.8), (1.0, 0.42, 0.05), (-2.6, -1.8, 0.375, 0.0)))
    parts.append(arm_cell("fanuc_m10", (-2.6, 1.8), (0.95, 0.75, 0.05), (-2.6, 1.8, 0.375, 0.0)))

    # Tool bench with a toolbox, wrench, hammer and screwdriver.
    parts.append(box_model("tool_bench_top", (2.6, 2.4, 0.375, 0.0), (1.2, 0.8, 0.05), (0.35, 0.28, 0.2)))
    for i, (dx, dy) in enumerate([(-0.5, -0.3), (-0.5, 0.3), (0.5, -0.3), (0.5, 0.3)]):
        parts.append(
            box_model(f"tool_bench_leg_{i}", (2.6 + dx, 2.4 + dy, 0.18, 0.0), (0.06, 0.06, 0.36), (0.25, 0.2, 0.15))
        )
    parts.append(box_model("toolbox", (2.35, 2.25, 0.47, 0.2), (0.36, 0.2, 0.14), (0.75, 0.1, 0.1)))
    parts.append(box_model("toolbox_lid", (2.35, 2.25, 0.55, 0.2), (0.36, 0.2, 0.02), (0.6, 0.08, 0.08)))
    # wrench: handle plus two prongs
    parts.append(box_model("wrench_handle", (2.75, 2.3, 0.42, 0.4), (0.02, 0.16, 0.012), (0.7, 0.7, 0.72)))
    parts.append(box_model("wrench_jaw_a", (2.82, 2.25, 0.42, 0.4), (0.02, 0.03, 0.02), (0.7, 0.7, 0.72)))
    parts.append(box_model("wrench_jaw_b", (2.82, 2.36, 0.42, 0.4), (0.02, 0.03, 0.02), (0.7, 0.7, 0.72)))
    # hammer: handle plus head
    parts.append(cylinder_model("hammer_handle", (2.05, 2.38, 0.45, 0.5), 0.012, 0.26, (0.55, 0.35, 0.15)))
    parts.append(box_model("hammer_head", (2.12, 2.42, 0.45, 0.5), (0.08, 0.04, 0.04), (0.3, 0.3, 0.32)))
    # screwdriver: handle plus shaft
    parts.append(cylinder_model("screwdriver_handle", (2.15, 2.25, 0.44, 1.1), 0.015, 0.09, (0.9, 0.75, 0.1)))
    parts.append(cylinder_model("screwdriver_shaft", (2.15, 2.31, 0.44, 1.1), 0.004, 0.13, (0.75, 0.75, 0.78)))

    parts.append(wall_camera())
    parts.append(FOOTER)
    return "".join(parts)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({len(build().splitlines())} lines)")
