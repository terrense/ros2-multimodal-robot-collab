# Gazebo / WSL Failure Log

Every one of these cost a restart cycle. Recorded so they are fixed once.

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | Topic discovery works, data never arrives | WSL `networkingMode=mirrored` breaks Fast DDS cross-process topics | CycloneDDS: `sudo apt install ros-humble-rmw-cyclonedds-cpp`, export `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` |
| 2 | Spawn service missing, ROS graph empty | `GAZEBO_MODEL_PATH` pointing at all of `/opt/ros/humble/share`; Gazebo scans thousands of dirs before plugins load | `scripts/setup_gazebo_models.sh`: symlink only the needed packages |
| 3 | `Failed to initialize scene`, gzserver/gzclient crash | setting `GAZEBO_RESOURCE_PATH` to a custom dir replaced Gazebo's own Ogre media path | never override `GAZEBO_RESOURCE_PATH` |
| 4 | Spawned MoveIt arms are invisible | planning URDFs have no `<inertial>`; Gazebo's URDF→SDF drops those links (SDF came out empty) | `scripts/ensure_urdf_inertials.py` adds a default inertial |
| 5 | Arms spawn but fall over on the table | same URDFs are dynamic once they have inertia; no controllers hold them | `scripts/make_static_sdf.py` converts to a static SDF before spawn |
| 6 | `spawn_entity.py` fails although the service exists | ros2cli discovery is unreliable in this WSL/CycloneDDS setup | `scripts/spawn_entity_direct.py` calls `/spawn_entity` with rclpy directly |
| 7 | Shell scripts fail with `set: pipefail` / `$'\r'` | scripts edited from Windows get CRLF | convert to LF before running (`scripts/*.sh`), git keeps `eol=lf` |
| 8 | Walls/boards render white, then Gazebo crashes | Ogre/PBR textures were not applied and, once a material script was added, scene init failed | decoration is geometry (boxes), no textures |
| 9 | Wall lettering mirrored from inside the room | text laid along +y on the east wall reads right-to-left from the interior | `reverse=True` lays letters along −y |
| 10 | `ros2 topic list`/`service list` returns empty while the graph works | ros2cli daemon under CycloneDDS | prefer rclpy graph probes (`node.get_service_names_and_types()`) in scripts |

## Spawn order that works

```bash
scripts/run_gazebo_demo.sh --workshop
# waits for Gazebo, then runs scripts/spawn_workshop_arms.sh:
#   render xacro → rewrite $(find …) and package:// → add inertials
#   → convert to static SDF → spawn via /spawn_entity (rclpy)
```

## Still open

- KUKA KR10 spawns in its zero-joint pose, which looks folded; posing it needs
  joint angles set in the rendered URDF before SDF conversion.
- Arms are static scene geometry: they do not move and have no controllers yet.
