# Gazebo L2 — Working End to End

Status: **navigation works**. A `navigation.navigate_to_station` goal sent from
the harness runtime drives the simulated robot from the spawn point to
`station_a` through SLAM + Nav2, and the ledger records the completed receipt.

Measured on 2026-11-09, WSL2 Ubuntu 22.04, ROS 2 Humble, Gazebo 11.

## Final result

```
tf at start:  map->base (0.00, 0.00)  map->odom (0.00, 0.00)
navigate_to_station: succeeded
odom: (0.0, 0.0) -> (2.99, 0.29)        # station_a is at (3.20, 0.40)
Nav2: "Reached the goal!" / "Goal succeeded"
ledger: navigation.navigate_to_station completed
tf after:     map->base (3.00, 0.30)  map->odom (0.03, 0.01)
```

## Three separate faults had to be fixed

### 1. WSL mirrored networking breaks Fast DDS

`.wslconfig` sets `networkingMode=mirrored`. Discovery and services worked;
cross-process topic data never arrived (a 1 Hz talker reached a pure-rclpy
subscriber 0 times). `ROS_LOCALHOST_ONLY=1` and
`FASTDDS_BUILTIN_TRANSPORTS=UDPv4` did not help.

Fix: `sudo apt install ros-humble-rmw-cyclonedds-cpp` and
`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`. Every script in this repository exports
it. First symptom seen by a user is `/clock`, `/scan`, `/tf` publishing but
`ros2 topic echo` staying empty.

### 2. Leaked processes fight over TF

Repeated launch/stop cycles left **seven** `vins_mono_bridge_node` processes and
a stale `static_transform_publisher` publishing an identity `map -> odom` while
slam_toolbox was also publishing it. Two publishers of the same transform made
SLAM's corrections look like they were being "cancelled", which produced a
completely misleading diagnosis (we spent time suspecting the lidar).

Fix: `scripts/stop_gazebo_demo.sh` now kills every node name this stack can
start (`vins_mono_bridge_node`, `static_transform_publisher`, all Nav2 servers,
perception, mission). It prints the remaining count, which must be 0.

Lesson recorded here because it cost the most time: **check for leftovers before
believing any TF or sensor measurement.**

### 3. slam_toolbox scan matching under-estimated motion

With a clean system and ground truth from Gazebo
(`gazebo_ros_state` plugin, topic `/model_states`):

| Measurement | Distance |
|---|---|
| Gazebo model pose (truth) | 0.862 m |
| Odometry | 0.862 m |
| SLAM `map -> base` | ~0.28 m |

Odometry was exact; the matcher was the problem. `transform_timeout: 0.2` also
produced `Message Filter dropping … queue is full`.

Fix, in `slam_toolbox_params.yaml`:

- `transform_timeout: 1.0`, `throttle_scans: 2` — drops went from recurring to
  one at startup;
- `use_scan_matching: false` — map is built from the (correct) wheel odometry.
  `map -> odom` stays identity, the map matches the world, and Nav2 plans on it.
  Re-enable only after the matcher issue is understood; the comment in the file
  says so.

This is a deliberate simplification for the simulation. It does not fix scan
matching; it removes it from the loop so the rest of the stack can be validated.

## Scan-matching experiments (matching is off; here is what was measured)

The block below records what was tried, so nobody repeats it blindly.

Ground-truth procedure: `gazebo_ros_state` publishes `/model_states`; a script
drives at a fixed speed and reports Gazebo pose, `/odom`, and `map -> base`.

| Configuration | Truth | Odom | SLAM | Tracked |
|---|---|---|---|---|
| Baseline (throttle 2, 0.25 s interval, barycenter on, 0.5 m search) | 0.862 m | 0.862 m | ~0.28 m | ~33% |
| Tuned (throttle 1, 0.1 s interval, barycenter off, 1.0 m search, response 0.05) | 0.953 m | 0.953 m | 0.799 m | 84% |
| Tuned + 0.005 correlation resolution + no loop closing | 1.001 m | 1.001 m | no `map -> odom` | stalled |
| Matching disabled (odometry-only mapping) | 0.862 m | 0.862 m | 0.862 m | 100% |

With the 84% configuration, navigation was still attempted and **failed**:
the robot ended at (2.47, 2.64) instead of (3.20, 0.40), the goal was canceled,
and the receipt/ledger correctly recorded `unknown / execution_uncertain`.
The remaining ~16% drift is enough to poison costmap poses over a 3 m path.

The 0.005-resolution variant stopped publishing `map -> odom` at all, most
likely because the correlation search became too expensive to keep up. It was
reverted immediately; the configuration currently in the repository is the
known-good one (matching disabled, throttle 2, 0.25 s, barycenter on).

Conclusion: scan matching can be made much better by removing the scan
barycenter and widening the correlation search, but not good enough for Nav2 in
this simulation. Do not re-enable it without repeating the ground-truth table
and a navigation run.

## Supporting changes

- `collab_lab.world`: adds the `gazebo_ros_state` plugin, so `/model_states`
  gives ground truth. Without it every "is the robot moving?" question is
  guesswork — this is what settled fault 3.
- `nav2_params.yaml`: the global costmap is a 20×20 rolling window built from
  live scans (no static map, unknown treated as free). It works both with SLAM
  and with the map→odom anchor.
- `gazebo_nav_vins_demo.launch.py`: publishes a static identity `map -> odom`
  when `start_slam:=false`, so station coordinates stay meaningful.
- `scripts/run_gazebo_demo.sh` / `scripts/stop_gazebo_demo.sh`: one-command
  start (window visible via WSLg) and a clean stop that leaves nothing behind.

## What is still not done

- **Scan matching is off.** The map is odometry-based; loop closure and drift
  correction are not exercised. The tuning experiments above reached 84%
  tracking and still broke navigation, so the feature is disabled until it can
  be validated the same way. Any change to the matcher must repeat both the
  ground-truth table and a navigation run.
- **No hardware.** Every backend here is Gazebo/simulated.
- **Alternatives considered, not adopted.** ORB-SLAM3 and VINS-Fusion expect
  stereo/RGB-D or visual-inertial input and output 3D poses that would need
  conversion to the 2D grid Nav2 consumes; LIO-SAM expects a 3D LiDAR, while
  this robot has a 2D ray sensor. For a 2D Nav2 stack, slam_toolbox remains the
  right tool — the failure was configuration and process hygiene, not the
  algorithm class.
- **L1 calibration against L2** (master plan Phase 6) can now start, but needs
  the fault taxonomy mapped onto what Gazebo can reproduce.

## Reproduce

```bash
scripts/run_gazebo_demo.sh --nav2     # SLAM + Nav2 + visible window
# in another shell:
python3 agent_harness/scripts/ros_tool_executor.py \
  /tmp/nav_plan.json --mode execute --engine runtime \
  --ledger /tmp/nav_ledger.db
scripts/stop_gazebo_demo.sh
```
