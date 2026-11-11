"""Every package a source file reaches for must be declared in a package.xml.

`rosdep install --from-paths src` installs exactly what the `package.xml` files
declare. Nothing else. So a package that is used but not declared works on a
machine where someone apt-installed it by hand, and fails in a clean container.

That is not hypothetical: `realsense2_description` and `velodyne_description`
are reached by `$(find ...)` from the robot's URDF, were never declared, and
resolved only because they happened to be installed here. A clean CI container
would have failed at `xacro`, and the failure would have looked like a broken
URDF rather than a missing declaration.

This test is the one that makes "it works on my machine" visible.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

FIND_XACRO = re.compile(r"\$\(find\s+([a-zA-Z0-9_]+)\)")
FIND_PACKAGE_SHARE = re.compile(r"FindPackageShare\(\s*[\"']([^\"']+)[\"']")
PACKAGE_KWARG = re.compile(r"package\s*=\s*[\"']([a-zA-Z0-9_]+)[\"']")
#: `from <pkg>.<module> import ...` / `import <pkg>.<module>` for these prefixes.
ROS_IMPORT = re.compile(r"^\s*(?:from|import)\s+((?:rclpy|tf2_ros|tf2_geometry_msgs|nav2_msgs|gazebo_msgs|sensor_msgs|std_msgs|geometry_msgs|nav_msgs|action_msgs|cv_bridge|ament_index_python)\b)", re.MULTILINE)

DEP_TAG = re.compile(r"<(?:exec|build|test|buildtool)_depend>\s*([^<\s]+)\s*</")


def _packages() -> dict[str, Path]:
    """package name -> its package.xml."""
    found: dict[str, Path] = {}
    for path in sorted(SRC.glob("*/package.xml")):
        root = ElementTree.parse(path).getroot()
        name = root.findtext("name")
        assert name, f"{path} has no <name>"
        found[name.strip()] = path
    return found


def _declared(packages: dict[str, Path]) -> set[str]:
    declared: set[str] = set()
    for path in packages.values():
        declared |= set(DEP_TAG.findall(path.read_text(encoding="utf-8")))
    return declared


def _references() -> dict[str, list[str]]:
    """referenced package -> files that reference it."""
    refs: dict[str, list[str]] = {}

    def note(package: str, path: Path) -> None:
        refs.setdefault(package, []).append(str(path.relative_to(REPO_ROOT)))

    for path in SRC.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix in {".xacro", ".urdf"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            for package in FIND_XACRO.findall(text):
                note(package, path)
        elif path.suffix == ".py":
            text = path.read_text(encoding="utf-8", errors="replace")
            for package in FIND_PACKAGE_SHARE.findall(text):
                note(package, path)
            for package in PACKAGE_KWARG.findall(text):
                note(package, path)
            for package in ROS_IMPORT.findall(text):
                note(package, path)
    return refs


def test_the_scan_finds_the_references_it_should() -> None:
    """A scan that matches nothing would make every check below vacuous."""
    refs = _references()
    for expected in ("realsense2_description", "velodyne_description", "gazebo_ros", "rclpy"):
        assert expected in refs, expected


def test_every_referenced_package_is_declared() -> None:
    packages = _packages()
    declared = _declared(packages)
    refs = _references()

    undeclared = {
        package: sorted(set(files))
        for package, files in refs.items()
        if package not in declared and package not in packages
    }
    assert not undeclared, (
        "these packages are used but not declared in any package.xml, so a clean "
        f"`rosdep install` will not provide them: {undeclared}"
    )


def test_the_sensor_descriptions_are_declared_explicitly() -> None:
    """Pinned: these two were the ones missing, and they are easy to lose again."""
    declared = _declared(_packages())
    assert "realsense2_description" in declared
    assert "velodyne_description" in declared


def test_every_declared_dependency_the_workspace_owns_exists() -> None:
    """A declared workspace package that no longer exists breaks the build."""
    packages = _packages()
    owned = {name for name in _declared(packages) if name.startswith("robot_collab_")}
    missing = sorted(owned - set(packages))
    assert not missing, f"declared but not present under src/: {missing}"


def test_every_package_declares_its_build_type() -> None:
    for name, path in _packages().items():
        text = path.read_text(encoding="utf-8")
        assert "<build_type>" in text, f"{name} has no <build_type>"


@pytest.mark.parametrize("package", sorted(_packages()))
def test_a_package_does_not_depend_on_itself(package: str) -> None:
    path = _packages()[package]
    assert package not in DEP_TAG.findall(path.read_text(encoding="utf-8"))
