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
    parameter: tuple[str, str, float] | None = None


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

    def close(self) -> None:
        self.node.destroy_node()
        if self._owns_context:
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

    # ------------------------------------------------------------ parameters

    def get_parameter(self, node_name: str, name: str) -> float | None:
        """Read a double parameter off a live node, or None if it is not there."""
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
        return None

    def set_parameter(self, node_name: str, name: str, value: float) -> bool:
        """Set a double parameter on a live node."""
        from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
        from rcl_interfaces.srv import SetParameters

        service = self._service(f"/{node_name}/set_parameters")
        if service is None:
            print(f"set {node_name}.{name}: the node has no parameter service")
            return False
        client = self.node.create_client(SetParameters, service)
        client.wait_for_service(timeout_sec=5.0)
        parameter = Parameter()
        parameter.name = name
        parameter.value = ParameterValue(
            type=ParameterType.PARAMETER_DOUBLE, double_value=float(value)
        )
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
        self._restore: dict[str, float] = {}

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
            x, y, z = resolve(prop, stations)
            self.ros.spawn(prop.name, prop.path, x, y, z)
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
        for prop in scene.props:
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
