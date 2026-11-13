"""The robot description has to survive the controller manager's parameter route.

`gazebo_ros2_control` hands the robot description to the controller manager
as a command-line parameter override, and rcl parses the value of an override
as YAML. A YAML plain scalar cannot hold a colon followed by a space, so a
single ``: `` in the description -- and the description is full of comments
that use one -- stops the controller manager from being created at all. The
symptom is a robot whose wheels never turn, and the cause is a comment.

These tests hold the description to the rule rcl enforces, so the failure
arrives as a red test rather than as a robot that quietly does not move.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
XACRO = REPO_ROOT / "src" / "robot_collab_sim" / "urdf" / "collab_mobile_base.urdf.xacro"
MODULE_PATH = REPO_ROOT / "src" / "robot_collab_sim" / "robot_collab_sim" / "robot_description.py"


def _module():
    """The sanitiser, loaded from the source tree.

    The module lives in the simulation package, which is only importable once
    the workspace has been built. The test suite runs without a build, so the
    module is loaded by path instead.
    """
    spec = importlib.util.spec_from_file_location("robot_description_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


robot_description = _module()


def test_comments_are_stripped_and_nothing_else_is():
    text = '<robot name="x">\n  <!-- a comment -->\n  <link name="a"/>\n</robot>\n'
    cleaned = robot_description.strip_comments(text)
    assert "<!--" not in cleaned
    assert '<robot name="x">' in cleaned
    assert '<link name="a"/>' in cleaned


def test_a_comment_that_spans_lines_is_stripped():
    text = '<robot name="x">\n  <!-- first\n     second -->\n  <link name="a"/>\n</robot>\n'
    cleaned = robot_description.strip_comments(text)
    assert "second" not in cleaned
    assert '<link name="a"/>' in cleaned


def test_a_colon_followed_by_a_space_is_reported():
    spots = robot_description.unsafe_spots('<robot name="x"><!-- note: here --></robot>')
    assert spots, "a colon followed by a space has to be reported"


def test_a_trailing_colon_is_reported():
    assert robot_description.unsafe_spots('<robot name="x">:')


def test_a_colon_that_yaml_can_carry_is_not_reported():
    # `package://` and `a:=b` are how a URDF says where a mesh is and how a
    # topic is remapped. Neither of them ends the value.
    assert robot_description.unsafe_spots('<mesh filename="package://pkg/a.dae"/>') == []
    assert robot_description.unsafe_spots("<remapping>a:=b</remapping>") == []


def test_the_report_names_the_line():
    spots = robot_description.unsafe_spots('<robot>\n  <link name="a"/>\n  <!-- note: here -->\n</robot>')
    assert any("line 3" in spot for spot in spots), spots


def test_the_repository_description_can_be_carried():
    """The description this repository ships must survive the parameter route.

    This is the guard that would have caught the controller manager that
    never appeared: the description is checked here, with the same rule rcl
    applies, at every commit that touches it.
    """
    source = XACRO.read_text(encoding="utf-8")
    cleaned = robot_description.strip_comments(source)
    spots = robot_description.unsafe_spots(cleaned)
    assert not spots, "the robot description cannot be carried as a parameter override:\n" + "\n".join(
        spots
    )
    assert robot_description.carries_as_parameter(cleaned)


def test_the_comments_are_what_would_have_broken_it():
    """The stripping has to be load-bearing, not decoration.

    If the comments were harmless the sanitiser would be dead code, and a
    reader would have no reason to keep it.
    """
    source = XACRO.read_text(encoding="utf-8")
    assert not robot_description.carries_as_parameter(
        source
    ), "the comments in the description no longer contain a bare ': '; the sanitiser may be obsolete"
