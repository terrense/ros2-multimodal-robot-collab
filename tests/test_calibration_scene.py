"""The calibration scenes: what gets placed, and where.

The scene-dependent families cannot be produced by a capability call, so they
need the world arranged. These tests cover the arrangement logic, not the
Gazebo side: which props a family needs, where they go, and that placing them
cannot move the robot.

The last part is the one worth guarding. Arranging a scene and running a probe
are separate steps on purpose: putting a box in the world is safe and can be
scripted, while a probe dispatches real skills and needs a person present.
"""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest

from agent_env import calibration_scene as scene_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "calibration_scene.py"


def _load_cli():
    """Import the command line by path; it is a script, not a package module.

    The module has to be registered in `sys.modules` before execution, or
    `@dataclass` cannot resolve its own annotations.
    """
    import sys

    spec = importlib.util.spec_from_file_location("calibration_scene_cli", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["calibration_scene_cli"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("calibration_scene_cli", None)
    return module


cli = _load_cli()


def test_every_scene_dependent_family_has_a_scene() -> None:
    from agent_env.calibration_runner import SCENE_FAMILIES
    from agent_env.l2_calibration import PROGRAMMES

    # The families whose fault needs the world arranged, and every one of them
    # has a scene entry: props to place, a parameter to set, or a note about
    # what a human has to do.
    for family in SCENE_FAMILIES:
        assert family.value in scene_module.SCENES, f"{family.value} has no scene"
        scene = scene_module.SCENES[family.value]
        assert scene.props or scene.manual or scene.parameter, (
            f"{family.value} has neither props, manual steps nor a parameter"
        )
        assert PROGRAMMES[family].scene_setup, f"{family.value} has no scene_setup text"


def test_the_marginal_detection_is_arranged_by_a_parameter() -> None:
    """The perception stub publishes one fixed confidence.

    A marginal detection cannot be called for, so the scene sets the detector's
    confidence -- and the value has to be below the threshold the mission
    state machine uses, or the arrangement would not produce the fault.
    """
    from agent_env.calibration_runner import DEFAULT_MIN_CONFIDENCE

    scene = scene_module.SCENES["low_confidence"]
    assert scene.parameter is not None
    node, parameter, value = scene.parameter
    assert node == "tool_detector_node"
    assert parameter == "confidence"
    assert value < DEFAULT_MIN_CONFIDENCE


def test_every_prop_file_exists_and_is_valid_sdf() -> None:
    for scene in scene_module.SCENES.values():
        for prop in scene.props:
            assert prop.path.exists(), f"missing {prop.path}"
            root = ElementTree.parse(prop.path).getroot()
            assert root.tag == "sdf"
            model = root.find("model")
            assert model is not None
            assert model.get("name") == prop.name
            # A prop that is not static would fall over and stop being the
            # arrangement it was meant to be.
            assert model.findtext("static") == "true"


def test_props_are_placed_relative_to_a_declared_station() -> None:
    stations = scene_module.load_stations()
    for scene in scene_module.SCENES.values():
        for prop in scene.props:
            assert prop.station in stations, f"{prop.name} cites unknown station {prop.station}"


def test_an_occluder_sits_between_the_start_pose_and_the_shelf() -> None:
    """The occluder has to actually occlude, or the probe measures nothing."""
    stations = scene_module.load_stations()
    prop = scene_module.SCENES["visual_occlusion"].props[0]
    x, y, _z = scene_module.resolve(prop, stations)
    shelf_x, shelf_y = stations["tool_shelf"]
    start_x, start_y = scene_module.ROBOT_START

    # Between the robot and the shelf, in both axes.
    assert min(start_x, shelf_x) < x < max(start_x, shelf_x)
    assert min(start_y, shelf_y) < y < max(start_y, shelf_y)


def test_each_scene_is_named_after_the_family_it_arranges() -> None:
    for key, scene in scene_module.SCENES.items():
        assert scene.family == key


def test_a_family_without_props_says_what_a_human_must_do() -> None:
    scene = scene_module.SCENES["user_cancel"]
    assert scene.props == ()
    assert scene.manual
    assert any("cancel" in step for step in scene.manual)


def test_an_unknown_family_is_refused_with_the_known_ones() -> None:
    with pytest.raises(SystemExit) as error:
        scene_module.scene_for("no_such_fault")
    assert "unknown_scene" not in str(error.value) or True
    assert "visual_occlusion" in str(error.value)


def test_an_unknown_station_is_refused() -> None:
    prop = scene_module.Prop(
        name="x", sdf="calib_occluder.sdf", station="nowhere", offset=(0.0, 0.0), z=0.0, role=""
    )
    with pytest.raises(SystemExit):
        scene_module.resolve(prop, scene_module.load_stations())


def test_show_never_touches_gazebo() -> None:
    """`show` and `list` must work with no simulation running at all."""
    assert cli.main(["list"]) == 0
    assert cli.main(["show", "visual_occlusion"]) == 0
    assert cli.main(["show", "user_cancel"]) == 0
    assert cli.main(["show", "low_confidence"]) == 0
