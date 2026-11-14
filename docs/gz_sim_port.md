# The stack in gz-sim

Gazebo Classic and gz-sim are two simulators. The robots are the same, the
runtime is the same, the contract is the same and the calibration is the
same; what changes is the simulator underneath and the plugins that talk to
it.

**This is a port that has not been run yet.** It was written against the
gz-sim (ros_gz) documentation, and the packages it needs are not installed
on this machine. Everything below that says "verified" was run; everything
else is what the documentation says, and the first run is where it gets
checked.

## What it needs

```bash
sudo apt-get install -y --no-install-recommends ros-humble-ros-gz gz-fortress
sudo apt-get install -y --no-install-recommends ros-humble-gz-ros2-control
```

The second one is the controller plugin gz-sim drives the wheels with. The
`ros-humble-ros-gz` metapackage does not include it, and without it the
base spawns with no motors: the simulator loads the description and says
`Failed to load system plugin [libgz_ros2_control.so]`.

Then:

```bash
bash scripts/run_gz_sim_demo.sh          # headless
bash scripts/run_gz_sim_demo.sh --gui    # with a window
```

The log is `/tmp/gz_sim_demo.log`.

The simulator itself is `ign gazebo` (Fortress is 6.x; the `gz` tool's `sim`
verb is Garden's), and the messages are `ignition.msgs.*`, not
`gz.msgs.*` — the create service rejects a `gz.msgs.EntityFactory` request
with "Unable to create request".

## What is different, and why

| Gazebo Classic | gz-sim | Why |
|---|---|---|
| `libgazebo_ros2_control.so` | `libgz_ros2_control.so` | gz-sim has its own controller plugin, and the hardware interface is `gz_ros2_control/GazeboSimSystem` |
| `libgazebo_ros_state.so` | `gz-sim-state-publisher-system` | the state publisher is a *system* in gz-sim, not a plugin |
| `<physics type="ode">` | `<physics type="ignored">` | gz-sim has its own default physics and says so |
| sensors publish on ROS topics | sensors publish on Gazebo Transport | `ros_gz_bridge` carries them across |
| the world file is the source | the world file is **generated** | `scripts/make_gz_sim_world.py` writes `collab_lab_gz.world` from `collab_lab.world`, so the two labs cannot drift apart |

## How the pieces are shared

- **One description, two simulators.** The robot is one URDF. The
  controller plugin is swapped by the render helper
  (`robot_description.py gz-sim`), because xacro evaluates a conditional on
  an argument *before* the argument is substituted, so a description that
  branches on a flag does not branch at all.
- **One world, two simulators.** The lab world is generated from the classic
  one; the furniture is the same furniture.
- **One robot description, two descriptions**: the camera's `<sensor>` block
  is parsed by gz-sim as a native camera and the ROS plugin inside it is
  ignored, so the same block serves both.

## The bridge

gz-sim's sensors publish on the Gazebo Transport; the stack expects ROS
topics. The bridge in `src/robot_collab_bringup/launch/gz_sim_demo.launch.py`
carries:

| Gazebo Transport | ROS |
|---|---|
| `/collab_mobile_base/front_rgbd_camera/image` | `/camera/color/image_raw` |
| `/collab_mobile_base/front_rgbd_camera/depth_image` | `/camera/depth/image_raw` |
| `/collab_mobile_base/front_rgbd_camera/points` | `/camera/depth/points` |
| `/collab_mobile_base/front_rgbd_camera/camera_info` | `/camera/color/camera_info` |
| `/collab_mobile_base/imu` | `/imu/data` |
| `/clock` | `/clock` |

**These names are the one thing here that was written from documentation
rather than from a running simulator.** The first thing to check is
`gz topic -l` while the simulator is up; if the names differ, the bridge
table is the only thing that has to change.

## What is not ported yet

- the 2D laser (`/scan`) — gz-sim has no ray sensor, and the navigation
  does not use the scan for anything that is measured (scan matching is
  off);
- the Velodyne's points — the official description carries a Gazebo Classic
  plugin, so it needs a gz-sim lidar block;
- the workshop scene's arms, which are spawned by a script.

## What is verified

- the world generator produces a world that differs from the classic one in
  exactly three places (`scripts/make_gz_sim_world.py` refuses to run if
  the classic world does not have them as written);
- **the world loads in gz-sim** — no errors, no missing models;
- **the robot spawns into it** and is in the world's model list;
- the description renders for both simulators, and the gz-sim one names
  gz-sim's controller plugin and nothing else;
- the classic stack is unaffected: the same description, the same world,
  the same calibration.

Three things gz-sim refuses that Gazebo Classic accepts, all found by
running it:

- **an inertia that breaks the triangle inequality.** The wheels' inertia
  had `izz + ixx < iyy`; Gazebo Classic loads it, gz-sim refuses the link
  ("A link named left_wheel_link has invalid inertia"). The numbers are
  the cylinder's own now.
- **a collision with a zero radius.** The official Velodyne description
  carries one; Gazebo Classic ignores a collision it cannot make, and
  gz-sim's physics asserts on it and takes the whole simulator down with it.
- **a sensor type it does not have.** The "ray" sensor (the 2D lidar and
  the VLP-16) is not supported; gz-sim wants a GPU LIDAR. The depth camera
  becomes an `rgbd_camera` with a depth block, and the ROS sensor plugins
  are dropped from the description (gz-sim would try to load them as
  systems and fail on every start).

## What is still open

- **the depth camera publishes only an image.** The sensor's topics are
  named and the description asks for a point cloud, but the cloud does not
  appear. The next thing to look at is the render engine: a sensor that
  cannot render publishes nothing.
- **the base has no motors** until `ros-humble-gz-ros2-control` is
  installed.
- the 2D lidar and the VLP-16's points, as above.
