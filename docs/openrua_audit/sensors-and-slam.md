# Sensors and SLAM Backends

The simulation now carries the sensor set that real SLAM stacks expect, and the
runtime has a **generic adapter** so the SLAM backend can be swapped without
touching Nav2 or the agent runtime.

## Sensors on the simulated robot

| Sensor | Simulated as | Topics | Measured |
|---|---|---|---|
| Intel RealSense **D435** | Gazebo `depth` camera, D435 depth FOV (87°) | `/camera/color/image_raw`, `/camera/color/camera_info`, `/camera/depth/image_raw`, `/camera/depth/points` | 640×480 @ 10 Hz, 60 msgs / 6 s |
| **16-line LiDAR** (VLP-16 class) | `ray` sensor, 16 rings × 720 columns, ±15°, 50 m | `/velodyne_points` (`PointCloud2`) | 5588 points/frame @ ~6.6 Hz |
| IMU | `gazebo_ros_imu` | `/imu/data` | 100 Hz |
| Wheel odometry | `gazebo_ros_diff_drive` | `/odom`, `odom -> base_footprint` | exact versus Gazebo ground truth |

Frames: `map → odom → base_footprint`; sensors at `camera_link (0.30, 0, 0.19)`,
`velodyne_link (-0.10, 0, 0.53)` (heights relative to `base_footprint`).

The depth camera publishes through the RealSense topic layout via remappings, so
existing perception nodes that subscribe to `/camera/color/image_raw` keep
working.

## The adapter: any SLAM backend, one contract

Nav2 needs exactly one thing from a SLAM system: `map → odom`. Backends differ
in what they publish — VINS and ORB-SLAM publish odometry in the map frame, LIO
publishes a cloud plus a pose, RTAB-Map publishes `map → odom` itself.

`robot_collab_slam/slam_adapter_node.py` collapses that difference:

```
backend publishes nav_msgs/Odometry (frame_id = map, child = base)
        │
        ▼
slam_adapter:  T_map_odom = T_map_base · T_odom_base⁻¹
        │
        ▼
TF map → odom        (the only SLAM contract Nav2 knows)
```

- parameters: `pose_topic`, `map_frame`, `odom_frame`, `base_frame`
- the math lives in `slam_math.py`, which has no ROS imports and 7 unit tests
  (`tests/test_slam_math.py`), including a rotation case and an inverse
  round-trip
- no backend-specific code, so adding ORB-SLAM3 or VINS is a launch-file change

## What was run today

**RTAB-Map, RGB-D, on the running simulation** (`rtabmap_launch`):

```
rgb   = /camera/color/image_raw
depth = /camera/depth/image_raw
odom  = /odom            (wheel odometry; visual_odometry disabled)
```

RTAB-Map processed frames at 1 Hz and published `map -> odom`. Then Nav2
(`navigation_launch.py`, our rolling costmap) navigated the robot:

```
odom: (0.09, 0.00) → (2.99, 0.31)     station_a is (3.20, 0.40)
receipt: completed
ledger:  navigation.navigate_to_station completed
map->odom after: (0.000, 0.000)
```

So the full chain works with a visual SLAM backend:
**D435 depth → RTAB-Map → map→odom → Nav2 → harness runtime → ledger.**

## Honest limits

- **Visual odometry is off.** RTAB-Map used wheel odometry for pose and its
  RGB-D data for the map and loop closure. Turning on `visual_odometry:=true`
  is the next step; the adapter already supports consuming its output.
- **The 3D LiDAR is not yet used by a LIO backend.** The cloud is published and
  the frame exists; LIO-SAM needs GTSAM (not in apt) and a build, which was not
  attempted here.
- **ORB-SLAM3 / VINS-Fusion were not built.** They are C++ projects with heavy
  dependencies; the adapter means the integration work is a topic mapping, not
  a redesign.
- **No hardware.** Everything above is Gazebo, simulated sensors, WSL2.

## Reproduce

```bash
# Gazebo + robot (keep it running)
scripts/run_gazebo_demo.sh

# RGB-D SLAM
ros2 launch rtabmap_launch rtabmap.launch.py \
  rgb_topic:=/camera/color/image_raw depth_topic:=/camera/depth/image_raw \
  camera_info_topic:=/camera/color/camera_info odom_topic:=/odom \
  frame_id:=base_footprint approx_sync:=true use_sim_time:=true \
  visual_odometry:=false rviz:=false

# a backend that only publishes odometry:
ros2 run robot_collab_slam slam_adapter_node --ros-args \
  -p use_sim_time:=true -p pose_topic:=/vins/odometry
```
