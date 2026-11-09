# Gazebo L2 Attempt — 2026-11-09

Bringing the Gazebo simulation up under WSL2, what worked, and the one thing
that does not. Commands and numbers are from this machine.

## Environment

- Ubuntu 22.04 on WSL 2 (kernel 6.18.5.4-1), WSLg available (`DISPLAY=:0`)
- ROS 2 Humble at `/opt/ros/humble`; Gazebo 11.10.2; `gazebo_ros` 3.9.0; Nav2 packages
- Workspace built: `colcon build --base-paths src` → 10 packages, 2 m 26 s

## A WSL-specific trap that had to be fixed first

`.wslconfig` sets `networkingMode=mirrored`. Under Fast DDS this produced the
worst failure mode: **discovery works, data does not**.

| Path | Fast DDS (mirrored) | CycloneDDS |
|---|---|---|
| service / action | works | works |
| intra-process topics | works | works |
| cross-process topics | **0 messages** | works |
| `/clock`, `/scan`, `/tf` | empty | populated |

Evidence: a ROS talker published at 1 Hz while a pure rclpy subscriber received
0 messages; `ROS_LOCALHOST_ONLY=1` and `FASTDDS_BUILTIN_TRANSPORTS=UDPv4` did not
help. After `apt install ros-humble-rmw-cyclonedds-cpp` and
`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`, the same subscriber received 6 messages
in 6 s and `ros2 topic echo` worked.

**Every ROS shell and script in this repository must export CycloneDDS on this
machine.** Both `scripts/run_gazebo_demo.sh` and
`scripts/smoke_ros2_runtime.sh` do.

## What works

- Gazebo GUI is visible through WSLg; the lab world loads and the robot spawns
  (`SpawnEntity: Successfully spawned entity [collab_mobile_base]`).
- The robot drives on `/cmd_vel`; odometry confirms motion
  (`before (0.0, 0.0) → after (0.75, 0.0)`, `distance_m=0.754`).
- Sensors publish: `/scan` at 9 Hz (360 beams, ~170 valid, range 0.19–7.71 m),
  `/odom`, `/imu/data`, `/camera/color/image_raw`, `/clock`.
- TF tree is complete: `map → odom → base_footprint → wheels`,
  `base_footprint → lidar_link (0.18, 0.00, 0.23)`.
- Nav2 + SLAM start: controller, planner, bt_navigator, lifecycle manager, both
  costmaps, slam_toolbox all present.
- The harness navigation path reaches Nav2: a
  `navigation.navigate_to_station` goal is accepted by the skill server, the
  mission stack forwards it, `bt_navigator` logs
  `Begin navigating from (0.00,-0.00) to (3.20,0.40)`, and the failure is
  recorded as a receipt and a ledger row (`failed / action_aborted`).

## What does not work: SLAM localization

The robot moves, the scans change, but SLAM pins the robot near the map origin:

```
odom -> base_footprint:  (3.45, 0.39)     # wheels say 3.45 m travelled
map  -> odom:            (-3.16, -0.61)   # SLAM cancels that motion
map  -> base_footprint:  (0.15, 0.31)     # so the robot "never moved"
```

Consequences: the global costmap has the goal cell marked `unknown`, the
NavFn planner refuses (`GridBased failed to generate a valid path to
(3.20, 0.40)`), and the navigation action aborts.

Diagnostics run, to rule causes out:

| Hypothesis | Test | Result |
|---|---|---|
| Lidar not publishing | `/scan` rate | 9.06 Hz, 360 beams |
| Lidar static | hash two scans across a 1.5 m drive | 73 distinct scans, hash changed |
| Missing TF | `base_footprint → lidar_link` | present (0.18, 0.00, 0.23) |
| QoS mismatch | publisher/subscriber reliability | publisher RELIABLE, subscribers BEST_EFFORT (compatible) |
| Message filter drops | slam log | present with `transform_timeout: 0.2`, gone after raising it to 1.0 |

One real defect was found and fixed: with `transform_timeout: 0.2` slam_toolbox
logged `Message Filter dropping message: frame 'lidar_link' … queue is full`.
`transform_timeout` is now 1.0 and `throttle_scans` is 2 in
`slam_toolbox_params.yaml`. The drop warnings disappeared, but the pose
divergence remained.

## Honest status

- **Phase 2/3 runtime on ROS 2: validated** against the simulated action stack
  (see `ros2-validation.md`), including goal-uuid correlation and the ledger.
- **Gazebo sensor layer: validated** (topics, TF, motion).
- **Nav2/SLAM navigation: NOT working.** The failure is in the simulation's
  SLAM configuration, not in the runtime: our code faithfully recorded the
  abort and the ledger shows a clean `failed` receipt with its goal uuid.

## Next steps (not done)

1. Compare `map → odom` over time while driving: if it is constant, SLAM is not
   processing new scans; if it moves, the matcher is locking onto the wrong
   place. This distinguishes "starvation" from "bad match".
2. Consider replacing online SLAM with a saved map + AMCL for the demo, which is
   the standard Nav2 path and removes scan-matching from the loop.
3. Fix the lidar noise configuration: Gazebo logs
   `Get noise index not valid` six times at startup.
4. Only after navigation works should L1 transition probabilities be calibrated
   against L2 (master plan Phase 6).
