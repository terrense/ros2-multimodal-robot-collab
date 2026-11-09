#!/usr/bin/env python3
"""Mount the official Dobot CR10 on our mobile base.

- strips the world/dummy frames,
- prefixes every link and joint with ``dobot_`` to avoid clashing with the
  mobile base's own ``base_link``,
- adds a fixed mount joint to the chassis,
- rewrites mesh paths to the copy vendored inside robot_collab_sim (so the
  file travels with the package),
- adds inertia where missing and joint damping so the arm does not droop.

Writes src/robot_collab_sim/urdf/dobot_mounted.urdf, which the robot xacro
includes.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

MOUNT_XYZ = "-0.15 0 0.22"
MOUNT_RPY = "0 0 0"
DROP = {"world", "dummy_link"}
DROP_JOINTS = {"world_joint", "dummy_joint"}


def _inertial_defaults(link: ET.Element) -> None:
    if link.find("inertial") is not None:
        return
    inertial = ET.Element("inertial")
    ET.SubElement(inertial, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
    ET.SubElement(inertial, "mass", {"value": "2.0"})
    ET.SubElement(
        inertial,
        "inertia",
        {"ixx": "0.05", "ixy": "0.0", "ixz": "0.0", "iyy": "0.05", "iyz": "0.0", "izz": "0.05"},
    )
    link.insert(0, inertial)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: mount_dobot.py IN.urdf OUT.urdf", file=sys.stderr)
        return 2
    source, target = argv
    tree = ET.parse(source)
    root = tree.getroot()

    for link in list(root.findall("link")):
        if link.get("name") in DROP:
            root.remove(link)
            continue
        link.set("name", f"dobot_{link.get('name')}")
        for mesh in link.iter("mesh"):
            filename = mesh.get("filename", "")
            mesh.set("filename", f"file://$(find robot_collab_sim)/meshes/dobot_cr10/{filename.rsplit('/', 1)[-1]}")
        _inertial_defaults(link)

    for joint in list(root.findall("joint")):
        if joint.get("name") in DROP_JOINTS:
            root.remove(joint)
            continue
        joint.set("name", f"dobot_{joint.get('name')}")
        for tag in ("parent", "child"):
            node = joint.find(tag)
            if node is not None:
                node.set("link", f"dobot_{node.get('link')}")
        if joint.get("type") == "revolute":
            dynamics = joint.find("dynamics")
            if dynamics is None:
                dynamics = ET.SubElement(joint, "dynamics")
            dynamics.set("damping", "50.0")
            dynamics.set("friction", "50.0")

    mount = ET.SubElement(root, "joint", {"name": "dobot_mount", "type": "fixed"})
    ET.SubElement(mount, "origin", {"xyz": MOUNT_XYZ, "rpy": MOUNT_RPY})
    ET.SubElement(mount, "parent", {"link": "base_link"})
    ET.SubElement(mount, "child", {"link": "dobot_base_link"})

    # xacro:include takes the children of the included document root, so the
    # file keeps its <robot> wrapper.
    tree.write(target, encoding="utf-8", xml_declaration=True)
    print(f"{target}: dobot mounted at {MOUNT_XYZ}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
