#!/usr/bin/env python3
"""Pose and recolour the official arm URDFs before spawning.

    prepare_arms.py kuka  IN.urdf OUT.urdf
    prepare_arms.py fanuc IN.urdf OUT.urdf
    prepare_arms.py dobot IN.urdf OUT.urdf

KUKA's zero-joint pose is folded flat; a standing pose is set by rotating the
revolute joints about their own axes. Colours follow the vendors:
KUKA orange/black, FANUC green, Dobot white with dark-blue base.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

KUKA_STANDING = {"joint_a2": -1.4, "joint_a3": 1.9, "joint_a5": -0.9}

COLORS = {
    "kuka": {
        "base": (0.05, 0.05, 0.05, 1.0),
        "link": (1.0, 0.35, 0.0, 1.0),
    },
    "fanuc": {
        "base": (0.10, 0.45, 0.15, 1.0),
        "link": (0.12, 0.60, 0.22, 1.0),
    },
    "dobot": {
        "base": (0.05, 0.12, 0.35, 1.0),
        "link": (0.92, 0.92, 0.92, 1.0),
    },
}


def _origin(joint: ET.Element) -> ET.Element:
    origin = joint.find("origin")
    if origin is None:
        origin = ET.SubElement(joint, "origin")
        origin.set("xyz", "0 0 0")
        origin.set("rpy", "0 0 0")
    return origin


def pose_kuka(root: ET.Element) -> None:
    for joint in root.findall("joint"):
        angle = KUKA_STANDING.get(joint.get("name", ""))
        if angle is None:
            continue
        _origin(joint).set("rpy", f"0 {angle} 0")


def recolour(root: ET.Element, brand: str) -> None:
    palette = COLORS[brand]
    for link in root.findall("link"):
        name = link.get("name", "")
        colour = palette["base"] if "base" in name else palette["link"]
        for visual in link.findall("visual"):
            material = visual.find("material")
            if material is None:
                material = ET.SubElement(visual, "material")
            colour_node = material.find("color")
            if colour_node is None:
                colour_node = ET.SubElement(material, "color")
            colour_node.set("rgba", " ".join(str(v) for v in colour))


GAZEBO_MATERIALS = {
    "kuka": {"base": "Gazebo/Black", "link": "Gazebo/Orange"},
    "fanuc": {"base": "Gazebo/DarkGrey", "link": "Gazebo/Green"},
    "dobot": {"base": "Gazebo/Blue", "link": "Gazebo/White"},
}


def add_gazebo_materials(root: ET.Element, brand: str) -> None:
    """Gazebo Classic ignores URDF <color>; it needs <gazebo><material>."""
    palette = GAZEBO_MATERIALS[brand]
    for link in root.findall("link"):
        name = link.get("name", "")
        material = palette["base"] if "base" in name else palette["link"]
        gazebo = ET.SubElement(root, "gazebo", {"reference": name})
        ET.SubElement(gazebo, "material").text = material


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: prepare_arms.py brand IN.urdf OUT.urdf", file=sys.stderr)
        return 2
    brand, source, target = argv
    tree = ET.parse(source)
    root = tree.getroot()
    if brand == "kuka":
        pose_kuka(root)
    if brand in COLORS:
        recolour(root, brand)
    if brand in GAZEBO_MATERIALS:
        add_gazebo_materials(root, brand)
    tree.write(target, encoding="utf-8", xml_declaration=True)
    print(f"{target}: {brand} prepared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
