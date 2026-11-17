# The stack in gz-sim

Gazebo Classic and gz-sim are two simulators. The robots are the same, the
runtime is the same, the contract is the same and the calibration is the
same; what changes is the simulator underneath and the plugins that talk to
it.

**This port has been run.** Everything below that says *verified* was
measured on this machine, and the numbers in it are what the stack
actually printed. What is not ported is in its own section, at the end,
and it is not dressed up.

## What it needs

```bash
sudo apt-get install -y --no-install-recommends ros-humble-ros-gz gz-fortress
```

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
| `libgazebo_ros2_control.so` | gz-sim's own `DiffDrive` system | `gz_ros2_control`'s Fortress build reads the controllers out of the server's own state and takes the whole simulator down with it (`ControllerManager::read` segfaults) |
| `libgazebo_ros_state.so` | the pose publisher system, plus a node of our own | the state publisher is a *system* in gz-sim, not a plugin, and it publishes on the Gazebo Transport |
| `<physics type="ode">` | `<physics type="bullet">` plus the physics plugin | gz-sim's physics is a plugin too, and `type="ignored"` is not a fast engine: it is the absence of one |
| sensors publish on ROS topics | sensors publish on Gazebo Transport | `ros_gz_bridge` carries them across |
| the world file is the source | the world file is **generated** | `scripts/make_gz_sim_world.py` writes `collab_lab_gz.world` from `collab_lab.world`, so the two labs cannot drift apart |

The engine that runs in the end is DART: gz-sim reads the `<physics>`
element itself, and the plugin loads the engine it finds first.

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
topics. gz-sim scopes a sensor's topic by the whole chain it hangs from —
world, model, link, sensor — so the Gazebo side's names are long, and the
ROS side keeps the names the rest of the stack knows:

| Gazebo Transport | ROS |
|---|---|
| `/world/collab_lab/model/collab_mobile_base/link/base_footprint/sensor/front_rgbd_camera/image` | `/camera/color/image_raw` |
| `.../front_rgbd_camera/depth_image` | `/camera/depth/image_raw` |
| `.../front_rgbd_camera/points` | `/camera/depth/points` |
| `.../front_rgbd_camera/camera_info` | `/camera/color/camera_info` |
| `.../base_imu/imu` | `/imu/data` |
| `/clock` | `/clock` |
| `/diff_drive_controller/odom` | `/diff_drive_controller/odom` |

The bridge is one-way, and that is not a detail: the bridge's form is
`topic@ROS_type[GZ_type`, and the bracket is the direction. A bridge
without one carries messages both ways, and a bridge that publishes on the
Gazebo clock's topic is a second publisher on it — enough for the
simulator to move its clock to another topic, where nobody is listening.
With the clock missing, every node with `use_sim_time` freezes and the
navigation waits for a timer that never fires.

## The command, and the truth

Two things do not come from the bridge, and both are nodes of this
repository's own (`src/robot_collab_gz_bridge`):

- **the command** (`twist_forwarder`). gz-sim's diff-drive system takes
  its command on a Gazebo Transport topic that has a subscriber and no
  publisher, and the bridge looks a topic's type up before it starts — so
  it finds no type and refuses the topic. The forwarder publishes the
  message itself, which needs no lookup.
- **the truth** (`gz_pose_to_model_states`). Gazebo Classic's state plugin
  published `/gazebo/model_states` and `/link_states`; gz-sim's pose
  publisher is a system of the world, and it has to be attached to a
  *model* — a world-level pose publisher fails with "PosePublisher plugin
  should be attached to a model entity". It also has to be told to publish
  a vector (`use_pose_vector_msg`), or it publishes one pose per entity,
  one after the other. The node turns that vector into the two messages
  the rest of the stack reads, with the names Gazebo Classic gave them.

## The window

The window is a **separate process**: `ign gazebo -g` means *the GUI only*.
A launch that passes `-g` to the one simulator process gets a window with
no server behind it — a black rectangle that asks for the list of worlds
for as long as it is open. The server runs headless (`-s`) and the GUI
(`-g`) connects to it over the transport.

The world also needs the scene broadcaster: a server without it simulates
perfectly and a window connected to it shows nothing, because there is no
scene to draw.

WSLg's clipboard bridge takes the GUI down when the Windows clipboard
changes under it (a crash in `QClipboard::emitChanged`). The window is
not restarted when that happens: a window that comes back on its own is a
window the person cannot get rid of, and the stack runs perfectly well
without it. `scripts/run_gz_sim_demo.sh --gui` opens it again.

## What is verified

Measured on this machine, with the numbers the stack printed:

- the world generator produces a world that differs from the classic one in
  exactly the places it says (`scripts/make_gz_sim_world.py` refuses to run
  if the classic world does not have them as written);
- **the world loads in gz-sim** with a physics engine, and the robot
  spawns into it;
- **the camera publishes** an image, a depth image, a point cloud and the
  camera info; the IMU publishes; the clock reaches ROS (1129 messages in
  5 s);
- **the base drives.** The command goes from the navigation, through the
  forwarder, onto the Gazebo Transport, into the diff-drive system, and the
  wheels turn: the odom counts 4.58 m of a commanded 25 s drive;
- **the navigation drives the base to a station.** Asking for
  `tool_shelf` puts the chassis at (1.15, -0.66) with the station at
  (1.25, -0.75); asking for `station_a` puts it at (3.06, 0.38) with the
  station at (3.20, 0.40). The odometry and the chassis agree to 0.014 m
  on that drive;
- **the navigation turns the base around.** Asking for `viewpoint_left`,
  which is behind the base, puts it at (0.00, 2.28) with the station at
  (0.00, 2.40) — the base pivots on the spot and drives off, which is
  what the `pivot_capable` parameter is for;
- **the window shows the world** and the robot in it, and the base drives
  while it is up;
- the classic stack is unaffected: the same description, the same world,
  the same calibration.

## Three things gz-sim refuses that Gazebo Classic accepts

All found by running it:

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

## What the navigation had to learn

Two things the base could not do, both found by driving it in gz-sim and
both fixed in `nav_skill_server.py` (the same file the classic stack
uses):

- **a station behind the base.** The base turns by driving in an arc, so
  a forward command with a hard turn makes it drive in a circle and come
  back to where it started. In gz-sim the base's wheels grip and it *can*
  rotate on the spot, and asking it to drive the arc instead is asking it
  to go in a circle. So the navigation asks whether the base can pivot
  (`pivot_capable`), and the launch states it: the gz-sim launch says yes,
  the classic launch says no, and with it off the behaviour is the one the
  classic stack's measurements were taken with.
- **a base that is pivoting is not stuck.** It has not gone anywhere while
  it turns, and the old detector called that stuck — so it backed up, into
  the crate behind it, and stayed there. A pivoting base is told apart
  from a jammed one by its yaw: a base that is jammed cannot turn at all,
  and the detour is for that.

## What is not ported yet

- **the workshop scene's arms.** They are spawned by a script, and their
  joints need a controller: `gz_ros2_control` is the one that is available,
  and it takes the simulator down. gz-sim's own joint controller systems
  would do the job, but the bridge from the ROS trajectory commands to the
  Gazebo Transport is not written.
- **the arms' joints in the lab scene** — the arm on the chassis is
  carried along as part of the base's description and is not driven.
- **the 2D lidar (`/scan`)** — gz-sim has no ray sensor, and the
  navigation does not use the scan for anything that is measured (scan
  matching is off).
- **the Velodyne's points** — the official description carries a Gazebo
  Classic plugin, so it needs a gz-sim lidar block.
- **the navigation cannot route around the workbench.** The drive is a
  straight line to the station, and the bench at (2.65, -0.55) stands
  between the tool shelf and `station_b`. The stations the calibration
  uses (`tool_shelf` and `station_a`) are clear of it, and the drive to
  `station_b` fails with the base against the bench. This is the classic
  stack's limitation too: the navigation is a wall follower, not a
  planner, and it says so.
