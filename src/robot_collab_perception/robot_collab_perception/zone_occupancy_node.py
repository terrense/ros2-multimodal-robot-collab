import math

import rclpy
from gazebo_msgs.msg import ModelStates
from rclpy.node import Node
from std_msgs.msg import Bool, String


class ZoneOccupancyNode(Node):
    """Tells the mission whether a handover zone is free.

    The mission used to hand the tool over whatever was in the way. That
    made a blocked handover impossible to produce: an obstruction at the
    handover station changed nothing, so the question "is a blocked handover
    unblocked by calling the operator?" had no answer on this stack.

    This node watches the workcell. It reads where everything is, and
    publishes, for every configured zone, whether something is standing in
    it.

    It also plays the operator. When the mission calls the operator over
    the speaker -- the same call the agent makes through `hri.publish_tts` --
    this node does what the human would do and takes the obstruction away.
    Without that, a mission that calls for help would wait forever for
    somebody who is not in the simulation.

    The model states come in on a topic rather than from a service call:
    the simulator's services are advertised but not answerable on every
    transport, and a monitor that silently never hears anything would
    report every zone as free.
    """

    def __init__(self) -> None:
        super().__init__("zone_occupancy_node")
        self.declare_parameter("zones", ["handover_zone"])
        self.declare_parameter("zone_radius", 0.55)
        self.declare_parameter("station_file", "")
        # Models that are part of the workcell rather than in the way: the
        # station markers, the floor, and the robot itself.
        self.declare_parameter(
            "ignore_models",
            [
                "ground_plane",
                "tool_shelf_marker",
                "station_a_marker",
                "station_b_marker",
                "handover_zone_marker",
                "collab_mobile_base",
                "sun",
            ],
        )
        self.declare_parameter("model_states_topic", "/model_states")
        self.declare_parameter("tts_topic", "/hri/tts_text")
        self.declare_parameter("world_frame", "map")

        self._zones = [str(zone) for zone in self.get_parameter("zones").value]
        self._radius = float(self.get_parameter("zone_radius").value)
        self._ignore = {str(name) for name in self.get_parameter("ignore_models").value}
        self._publishers = {
            zone: self.create_publisher(Bool, f"/safety/zone_occupancy/{zone}", 5)
            for zone in self._zones
        }
        self._tts_sub = self.create_subscription(
            String,
            str(self.get_parameter("tts_topic").value),
            self._on_tts,
            10,
        )
        self._states_sub = self.create_subscription(
            ModelStates,
            str(self.get_parameter("model_states_topic").value),
            self._on_model_states,
            5,
        )
        self._stations = self._load_stations()
        self._model_positions: dict[str, tuple[float, float]] = {}
        self._heard = False
        #: What each zone was last reported as.
        self._last: dict[str, bool] = {}

        from gazebo_msgs.srv import DeleteEntity

        self._DeleteEntity = DeleteEntity
        self._delete_client = self.create_client(DeleteEntity, self._find_service("delete_entity"))

        self.get_logger().info(
            f"Zone occupancy monitor is ready: zones={self._zones}, radius={self._radius:.2f} m."
        )

    # ----------------------------------------------------------------- inputs

    def _load_stations(self) -> dict[str, tuple[float, float]]:
        """Where the zones are, from the station registry."""
        station_file = str(self.get_parameter("station_file").value).strip()
        if not station_file:
            return {}
        try:
            import yaml

            with open(station_file, encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except Exception as error:  # noqa: BLE001 - the monitor still works
            self.get_logger().warn(f"Could not read the station registry: {error}")
            return {}
        stations: dict[str, tuple[float, float]] = {}
        for name, entry in (data.get("stations") or {}).items():
            pose = (entry or {}).get("pose") or {}
            stations[str(name)] = (float(pose.get("x", 0.0)), float(pose.get("y", 0.0)))
        return stations

    def _find_service(self, name: str) -> str:
        """The simulator's service for a name, preferring the plain one."""
        for candidate, _types in self.get_service_names_and_types():
            if candidate == f"/{name}":
                return candidate
        for candidate, _types in self.get_service_names_and_types():
            if candidate.endswith(f"/{name}"):
                return candidate
        return f"/{name}"

    def _on_model_states(self, msg: ModelStates) -> None:
        """Where everything is."""
        positions: dict[str, tuple[float, float]] = {}
        for name, pose in zip(msg.name, msg.pose, strict=False):
            if name in self._ignore:
                continue
            positions[str(name)] = (float(pose.position.x), float(pose.position.y))
        self._model_positions = positions
        if not self._heard:
            self._heard = True
            self.get_logger().info(
                f"Watching {len(positions)} models for the occupancy of {self._zones}."
            )
        self._publish()

    def _on_tts(self, msg: String) -> None:
        """The operator has been called: clear the zone they were asked about."""
        text = str(msg.data)
        for zone in self._zones:
            if zone not in text:
                continue
            removed = self._clear_zone(zone)
            self.get_logger().warn(
                f"Called to clear {zone}; took {removed} obstruction(s) away."
            )

    # ------------------------------------------------------------------ world

    def _clear_zone(self, zone: str) -> int:
        """Remove whatever is standing in a zone."""
        position = self._stations.get(zone)
        if position is None:
            return 0
        removed = 0
        for name, (x, y) in list(self._model_positions.items()):
            if name in self._ignore:
                continue
            if math.hypot(x - position[0], y - position[1]) > self._radius:
                continue
            if not self._delete_client.service_is_ready():
                self.get_logger().warn(
                    "the simulator's delete service is not answering; "
                    "the obstruction stays where it is"
                )
                return removed
            request = self._DeleteEntity.Request()
            request.name = name
            future = self._delete_client.call_async(request)
            future.add_done_callback(lambda _future, entity=name: self._on_deleted(entity))
            removed += 1
        return removed

    def _on_deleted(self, name: str) -> None:
        self._model_positions.pop(name, None)

    # ------------------------------------------------------------- publishing

    def _publish(self) -> None:
        """Say whether each zone is free."""
        for zone in self._zones:
            position = self._stations.get(zone)
            occupied = False
            if position is not None:
                for name, (x, y) in self._model_positions.items():
                    if name in self._ignore:
                        continue
                    if math.hypot(x - position[0], y - position[1]) <= self._radius:
                        occupied = True
                        break
            if self._last.get(zone) != occupied:
                self.get_logger().warn(f"The {zone} is now occupied={occupied}.")
            self._last[zone] = occupied
            message = Bool()
            message.data = occupied
            self._publishers[zone].publish(message)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = ZoneOccupancyNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
