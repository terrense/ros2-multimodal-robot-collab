"""Arranging the world for a calibration probe.

Some fault families cannot be produced by calling a capability. A visual
occlusion needs something in front of the tool; a marginal detection needs a
detector that is not sure; a blocked handover needs the station occupied.
This module says what each of them needs and does it.

**Nothing here moves the robot.** The driver spawns and deletes static props
and sets node parameters. It never dispatches a capability, so arranging a
scene is not the same as moving the robot -- which is why it is a separate
step from the probes in `calibration_runner.py`.

Props are placed at fixed offsets from a named station rather than at
hard-coded world coordinates, so a scene stays attached to a station if the
station registry changes. The offsets are the only numbers that need touching
when the layout moves.

The command line lives in `scripts/calibration_scene.py`. This is the library
the calibration runner imports, and it lives in the package because the ROS
installation ships a `scripts` package of its own (`gazebo_ros` installs one),
and that one shadows the repository's `scripts/` directory for any import that
is not by path.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENE_DIR = REPO_ROOT / "src" / "robot_collab_sim" / "models" / "calibration"
STATIONS_YAML = REPO_ROOT / "src" / "robot_collab_bringup" / "config" / "stations.yaml"

#: Where the base starts, so a prop can be placed between it and a station.
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
    #: How far along the line of sight from the robot's start pose to the
    #: station the prop stands, as a fraction. When it is set, the prop is
    #: placed on that line instead of at the offset, and turned to face
    #: along it.
    along: float | None = None
    #: A fixed heading, in radians. Nothing to set it with it.
    yaw: float | None = None

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
    #: A node parameter the scene sets, as (node, parameter, value). The
    #: value that was there before is restored when the scene is cleared.
    parameter: tuple[str, str, float | str] | None = None


SCENES: dict[str, Scene] = {
    "visual_occlusion": Scene(
        family="visual_occlusion",
        props=(
            Prop(
                name="calib_occluder",
                sdf="calib_occluder.sdf",
                station="tool_shelf",
                offset=(0.0, 0.0),
                z=0.0,
                role="blocks the D435's view of the shelf from the start pose",
                # The panel stands on the line of sight from where the base
                # starts to the shelf, so the detector's ray to the tool
                # meets the panel instead of the tool. A short move to the
                # side clears it, which is what the probe measures.
                along=0.62,
            ),
        ),
        note="the base starts where the shelf is visible but the tool is not",
    ),
    "tool_drop": Scene(
        family="tool_drop",
        props=(),
        note="the arm's gripper lets go in transit, and the tool falls where it fell",
        # The arm's grip is what fails. A gripper that never lets go cannot
        # answer the question, and a scene that arranges the world by
        # spawning a prop cannot make a gripper slip.
        parameter=("arm_skill_server", "fault_mode", "drop"),
        manual=(
            "the arm releases the tool in transit; the place it fell is published "
            "on /manipulation/tool_dropped",
            "the second attempt has to pick the tool up from that place",
        ),
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
    "grasp_failure": Scene(
        family="grasp_failure",
        props=(),
        note="the gripper closes on nothing once, and the next attempt works",
        # A gripper that always closes on the tool cannot answer the
        # question. The fault is one miss, not a broken gripper: the second
        # attempt is what the recovery rate measures.
        parameter=("arm_skill_server", "fault_mode", "miss_once"),
        manual=(
            "the first grasp attempt closes on nothing; the second one works",
        ),
    ),
    "low_confidence": Scene(
        family="low_confidence",
        props=(),
        note="the detector must publish a confidence below the tool threshold",
        # The perception stub publishes one fixed confidence, so a marginal
        # detection has to be arranged rather than called for. The parameter is
        # the arrangement: nothing else about the world changes.
        parameter=("tool_detector_node", "confidence", 0.40),
        manual=(
            "the YOLO stub publishes one fixed confidence, so a marginal detection has "
            "to be arranged rather than called for",
            "either move the tool to the edge of the camera's range, dim the light over "
            "the shelf, or set front_rgbd/lighting so the detection falls below 0.70",
            "read /perception/tool_detections and confirm the published confidence is "
            "below the threshold before running the probe",
        ),
    ),
    "tool_missing": Scene(
        family="tool_missing",
        props=(),
        note="the requested tool must not be in the scene at all",
        manual=(
            "there is no 'detect this tool' capability: the YOLO stub publishes "
            "whatever its tool_id parameter names",
            "set the detector's tool_id to something that is not in the world, or remove "
            "the tool from the shelf",
            "read /perception/tool_detections and confirm no detection for it is "
            "published before running the probe",
        ),
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


def resolve_pose(
    prop: Prop, stations: dict[str, tuple[float, float]]
) -> tuple[float, float, float, float]:
    """Where to spawn a prop and which way it faces.

    A prop that stands on the line of sight is placed there rather than at
    an offset, because "on the line of sight" is the arrangement: a panel
    that is a hand's width to the side of the ray does not block anything.
    """
    if prop.along is None:
        x, y, z = resolve(prop, stations)
        return (x, y, z, float(prop.yaw or 0.0))

    if prop.station not in stations:
        raise SystemExit(
            f"{prop.name} is positioned relative to {prop.station!r}, "
            f"which is not in {STATIONS_YAML}"
        )
    target_x, target_y = stations[prop.station]
    dx = target_x - ROBOT_START[0]
    dy = target_y - ROBOT_START[1]
    x = ROBOT_START[0] + dx * prop.along
    y = ROBOT_START[1] + dy * prop.along
    # The panel faces along the line of sight, so that it is edge-on to
    # anybody looking from the side.
    yaw = math.atan2(dy, dx) + math.pi / 2.0
    return (round(x, 3), round(y, 3), prop.z, round(yaw, 4))


def scene_for(family: str) -> Scene:
    scene = SCENES.get(family)
    if scene is None:
        known = ", ".join(sorted(SCENES))
        raise SystemExit(f"unknown scene {family!r}; known: {known}")
    return scene


# ---------------------------------------------------------------------- ros


class RosScene:
    """Spawn entities, delete them, and read and write node parameters."""

    def __init__(self) -> None:
        import rclpy
        from gazebo_msgs.srv import DeleteEntity, SpawnEntity

        self.rclpy = rclpy
        self.DeleteEntity = DeleteEntity
        self.SpawnEntity = SpawnEntity
        # The runtime client has usually initialised the default context
        # already, and rclpy refuses a second initialisation. Owning the
        # context is recorded, because shutting down a context somebody else
        # owns would take the whole stack down with it.
        self._owns_context = not self.rclpy.ok()
        if self._owns_context:
            self.rclpy.init()
        self.node = self.rclpy.create_node("calibration_scene")
        #: The models the world currently has. The simulator's own list is
        #: the only account of whether a spawn or a delete took effect, and
        #: a scene that arranges the world and then finds it unchanged has
        #: arranged nothing.
        self._models: set[str] = set()
        #: Whether the world's list of models has arrived at all. Before it
        #: does, "the model is not there" is not a fact.
        self._heard = False
        from gazebo_msgs.msg import ModelStates

        self.node.create_subscription(
            ModelStates, "/model_states", self._on_model_states, 5
        )

    def close(self) -> None:
        self.node.destroy_node()
        if self._owns_context:
            self.rclpy.shutdown()

    def _service(self, fragment: str, timeout: float = 10.0):
        """Find one of the simulator's services.

        The plain name is preferred: the simulator advertises the same
        service twice, once plain and once under `/gazebo`, and only one of
        the two answers on every transport.
        """
        end = time.time() + timeout
        while time.time() < end:
            names = [name for name, _ in self.node.get_service_names_and_types() if fragment in name]
            for name in names:
                if name == f"/{fragment}":
                    return name
            if names:
                return names[0]
            self.rclpy.spin_once(self.node, timeout_sec=0.2)
        return None

    def _on_model_states(self, msg) -> None:
        self._models = {str(name) for name in msg.name}
        self._heard = True

    def _wait_for_model(self, name: str, present: bool, timeout: float = 8.0) -> bool:
        """Wait until the world agrees about a model.

        A spawn that the simulator accepted but did not perform, and a
        delete that is still in flight when the next spawn asks for the same
        name, both look like success to the caller. The world's own list is
        the only way to tell.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.rclpy.spin_once(self.node, timeout_sec=0.1)
            if (name in self._models) == present:
                return True
        return False

    def spawn(
        self, name: str, path: Path, x: float, y: float, z: float, yaw: float = 0.0
    ) -> bool:
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
        request.initial_pose.orientation.z = math.sin(yaw / 2.0)
        request.initial_pose.orientation.w = math.cos(yaw / 2.0)
        future = client.call_async(request)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=30.0)
        result = future.result()
        ok = bool(result and result.success)
        if ok and not self._wait_for_model(name, present=True):
            # The simulator said yes and the world disagrees: the prop is
            # not there, and a measurement against a world without it is a
            # measurement of nothing.
            print(f"spawn {name}: the simulator accepted it and the world has no such model")
            return False
        print(f"spawn {name} at ({x}, {y}, {z}): {'ok' if ok else (result.status_message if result else 'timeout')}")
        return ok

    def delete(self, name: str) -> bool:
        """Take a model out of the world.

        A model that is not there is left alone. Asking the simulator to
        delete something that has already gone produces a request whose
        effect arrives later, and a late delete removes whatever was spawned
        under that name in the meantime: the calibration would arrange the
        scene, watch the obstruction appear, and then find it gone.
        """
        if name not in self._models and self._heard:
            print(f"delete {name}: not present")
            return True
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
        if ok and not self._wait_for_model(name, present=False):
            print(f"delete {name}: the simulator accepted it and the world still has it")
            return False
        print(f"delete {name}: {'ok' if ok else 'not present'}")
        return ok

    # ------------------------------------------------------------ parameters

    def get_parameter(self, node_name: str, name: str) -> float | str | None:
        """Read a parameter off a live node, or None if it is not there."""
        from rcl_interfaces.msg import ParameterType
        from rcl_interfaces.srv import GetParameters

        service = self._service(f"/{node_name}/get_parameters")
        if service is None:
            return None
        client = self.node.create_client(GetParameters, service)
        client.wait_for_service(timeout_sec=5.0)
        request = GetParameters.Request()
        request.names = [name]
        future = client.call_async(request)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        result = future.result()
        if not result or not result.values:
            return None
        value = result.values[0]
        if value.type == ParameterType.PARAMETER_DOUBLE:
            return float(value.double_value)
        if value.type == ParameterType.PARAMETER_INTEGER:
            return float(value.integer_value)
        if value.type == ParameterType.PARAMETER_STRING:
            return str(value.string_value)
        return None

    def set_parameter(self, node_name: str, name: str, value: float | str) -> bool:
        """Set a parameter on a live node."""
        from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
        from rcl_interfaces.srv import SetParameters

        service = self._service(f"/{node_name}/set_parameters")
        if service is None:
            print(f"set {node_name}.{name}: the node has no parameter service")
            return False
        client = self.node.create_client(SetParameters, service)
        client.wait_for_service(timeout_sec=5.0)
        if isinstance(value, str):
            parameter_value = ParameterValue(
                type=ParameterType.PARAMETER_STRING, string_value=value
            )
        else:
            parameter_value = ParameterValue(
                type=ParameterType.PARAMETER_DOUBLE, double_value=float(value)
            )
        parameter = Parameter()
        parameter.name = name
        parameter.value = parameter_value
        request = SetParameters.Request()
        request.parameters = [parameter]
        future = client.call_async(request)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=10.0)
        result = future.result()
        ok = bool(result and result.results and result.results[0].successful)
        print(f"set {node_name}.{name} = {value}: {'ok' if ok else 'refused'}")
        return ok


class CalibrationSceneDriver:
    """Arrange the world for a probe, and put it back afterwards.

    Two kinds of arrangement, and both of them leave the robot alone:

        props        a static model is spawned next to a station, or deleted
        parameters   a node's parameter is set, or restored

    The parameter case is what makes a marginal detection possible at all: the
    perception stub publishes one fixed confidence, so the only way to ask the
    stack what a marginal detection does is to make it publish one.

    The driver never dispatches a capability. Arranging a scene is not the
    same as moving the robot, and the probes are what move it.
    """

    def __init__(self, stations_path: Path = STATIONS_YAML) -> None:
        self._stations_path = stations_path
        self._ros: RosScene | None = None
        #: The parameter value that was there before the scene was armed.
        self._restore: dict[str, float | str] = {}
        #: The names this driver has spawned, per family.
        self._spawned: dict[str, list[str]] = {}
        #: How many props have been spawned in total.
        self._counter = 0

    @property
    def ros(self) -> RosScene:
        if self._ros is None:
            self._ros = RosScene()
        return self._ros

    def close(self) -> None:
        if self._ros is not None:
            self._ros.close()
            self._ros = None

    def arm(self, family: object) -> None:
        """Arrange the world for one family."""
        name = getattr(family, "value", str(family))
        scene = SCENES.get(name)
        if scene is None:
            return
        stations = load_stations(self._stations_path)
        for prop in scene.props:
            x, y, z, yaw = resolve_pose(prop, stations)
            # A fresh name every time. The simulator refuses to spawn a
            # model under a name it has seen before, even after the model
            # has been deleted: the scene would be arranged, the spawn
            # refused, and the probe would measure a world without its
            # obstruction.
            self._counter += 1
            spawn_name = f"{prop.name}_{self._counter}"
            if self.ros.spawn(spawn_name, prop.path, x, y, z, yaw):
                self._spawned.setdefault(name, []).append(spawn_name)
        if scene.parameter is not None:
            node_name, parameter, value = scene.parameter
            previous = self.ros.get_parameter(node_name, parameter)
            if previous is not None:
                self._restore[f"{node_name}/{parameter}"] = previous
            self.ros.set_parameter(node_name, parameter, value)

    def clear(self, family: object) -> None:
        """Put the world back the way it was."""
        name = getattr(family, "value", str(family))
        scene = SCENES.get(name)
        if scene is None:
            return
        for spawn_name in self._spawned.pop(name, []):
            self.ros.delete(spawn_name)
        for prop in scene.props:
            # Also the plain name: a scene arranged by an older version of
            # this driver put its props there.
            self.ros.delete(prop.name)
        if scene.parameter is not None:
            node_name, parameter, _value = scene.parameter
            key = f"{node_name}/{parameter}"
            previous = self._restore.pop(key, None)
            if previous is not None:
                self.ros.set_parameter(node_name, parameter, previous)


__all__ = [
    "ROBOT_START",
    "SCENE_DIR",
    "SCENES",
    "STATIONS_YAML",
    "CalibrationSceneDriver",
    "Prop",
    "RosScene",
    "Scene",
    "load_stations",
    "resolve",
    "scene_for",
]
