#!/usr/bin/env python3
"""Write the gz-sim version of the workshop world from the Gazebo Classic one.

The same job `scripts/make_gz_sim_world.py` does for the lab, with the
arms in the file as well.

In the classic stack the arms are spawned into the running world by
`scripts/spawn_workshop_arms.sh`, and the robot is spawned by the launch.
In gz-sim neither can be: the robot has to be in the world file, because
the diff-drive system is configured before anything is spawned into the
world and needs its joints to be there already. The arms are static
models, and a static model needs no controller to be placed -- so they go
in the file too, and the world holds the whole workcell.

The arms' geometry is the official description, rendered by the same
script that spawns them in the classic stack (in its render-only mode),
so the two simulators show the same arms.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_gz_sim_world import (  # noqa: E402
    GZ_PHYSICS,
    GZ_PLUGINS,
    GZ_SUN,
    robot_model,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "src" / "robot_collab_sim" / "worlds" / "workshop.world"
TARGET = REPO_ROOT / "src" / "robot_collab_sim" / "worlds" / "workshop_gz.world"

CLASSIC_PHYSICS = """    <physics name="default_physics" default="0" type="ode">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
      <real_time_update_rate>1000</real_time_update_rate>
    </physics>"""

CLASSIC_STATE_PLUGIN = """    <plugin name="gazebo_ros_state" filename="libgazebo_ros_state.so">
      <ros><namespace>/</namespace></ros>
      <update_rate>30.0</update_rate>
    </plugin>"""

CLASSIC_GROUND = "    <include><uri>../models/ground_plane</uri></include>"
CLASSIC_SUN = "    <include><uri>model://sun</uri></include>"

#: The arms, and where they stand. The poses are the ones the classic
#: stack spawns them at.
ARMS = (
    ("fanuc_m10ia_real", -2.6, 1.8, 0.42),
    ("kuka_kr10_real", -2.6, -1.8, 0.42),
)


def arm_models(directory: Path) -> str:
    """The arms, as static SDF models, rendered from the official descriptions.

    The rendering is `scripts/spawn_workshop_arms.sh --render-only`, the
    same script that spawns them into the classic stack: one place that
    knows how the official descriptions are put together.
    """
    models = []
    for name, x, y, z in ARMS:
        path = directory / f"{name}.sdf"
        if not path.exists():
            raise SystemExit(
                f"the arm {name} is not in {directory}; render the arms first:\n"
                "  bash scripts/spawn_workshop_arms.sh --render-only "
                f"{directory}"
            )
        text = path.read_text(encoding="utf-8")
        # The converter writes the newest SDF it knows; Fortress reads 1.8.
        text = text.replace("version='1.9'", "version='1.8'").replace(
            'version="1.9"', 'version="1.8"'
        )
        # The model carries its own pose element; the world places it.
        models.append(_with_pose(text, x, y, z))
    return "\n".join(models)


def _with_pose(model: str, x: float, y: float, z: float) -> str:
    """The model, standing where it stands in the classic workshop."""
    marker = "<pose>"
    if marker in model:
        start = model.index(marker)
        end = model.index("</pose>", start) + len("</pose>")
        return model[:start] + f"<pose>{x} {y} {z} 0 0 0</pose>" + model[end:]
    index = model.index(">") + 1
    return model[:index] + f"\n    <pose>{x} {y} {z} 0 0 0</pose>" + model[index:]


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        directory = Path(argv[1])
    else:
        directory = Path("/tmp/workshop_arms")
    text = SOURCE.read_text(encoding="utf-8")
    for old, new, what in (
        ('<sdf version="1.6">', '<sdf version="1.8">', "the SDF version"),
        (CLASSIC_PHYSICS, GZ_PHYSICS, "the physics block"),
        (CLASSIC_STATE_PLUGIN, GZ_PLUGINS, "the state publisher"),
        (CLASSIC_SUN, GZ_SUN, "the sun"),
    ):
        if old not in text:
            raise SystemExit(f"the workshop world does not have {what} as written")
        text = text.replace(old, new)

    # gz-sim resolves a model URI against the resource path, not against the
    # world file, so the floor gets an absolute path -- the bullet floor,
    # because the classic one carries its contact parameters for ODE.
    floor = (
        REPO_ROOT
        / "install"
        / "robot_collab_sim"
        / "share"
        / "robot_collab_sim"
        / "models"
        / "ground_plane_gz"
    )
    text = text.replace(
        CLASSIC_GROUND, f"    <include><uri>{floor}</uri></include>"
    )

    arms = arm_models(directory)

    index = text.index("</world>")
    text = (
        text[:index]
        + arms
        + "\n"
        + robot_model()
        + "\n"
        + text[index:]
    )
    TARGET.write_text(text, encoding="utf-8")
    print(f"-> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
