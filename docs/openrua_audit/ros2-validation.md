# ROS 2 Validation 鈥?2026-11-09

First run of the Phase 2/3 runtime against a **live ROS 2 graph**. This
document records exact commands and outputs; everything not listed here was not
run.

## Environment

| Item | Value |
|---|---|
| Host | Windows 11, WSL 2.0.3.0, kernel 6.18.5.4-1 |
| Distribution | Ubuntu 22.04, user `shenxin`, x86_64, 943 GB free |
| ROS 2 | Humble, pre-installed at `/opt/ros/humble` |
| Toolchain | `colcon` and `rosdep` present; rosdep cache and `20-default.list` present |
| Dependencies | `launch_ros`, `nav2_msgs`, `robot_state_publisher`, `slam_toolbox`, `tf2_ros`, `xacro` all resolvable; `rosdep install` reported "All required rosdeps installed successfully" with `--skip-keys ament_python` |

## Build

```bash
cd /mnt/d/study_claudecode/ros2-multimodal-robot-collab-main
source /opt/ros/humble/setup.bash
rm -rf build install log          # Windows-generated leftovers, gitignored
colcon build --base-paths src
```

Result:

```
Summary: 10 packages finished [2min 26s]
colcon_exit=0
```

This is the first time the ten packages were built on a machine we control.
It matches the CI `ros2-build` job, which had only ever run inside the GitHub
container.

## End-to-end smoke

```bash
bash scripts/smoke_ros2_runtime.sh
```

1. `ros2 launch robot_collab_bringup demo_sim.launch.py` starts eight nodes
   (mission state machine, nav/arm skill servers, three perception nodes,
   voice gateway, agent gateway) with simulated backends.
2. `ros2 service call /system/query_state` returned:

```
mission_id='' state='IDLE'
active_nodes=['mission_state_machine', 'nav_skill_server', 'arm_skill_server',
              'tool_detector_node', 'face_auth_node', 'voice_gateway_node',
              'agent_gateway_node']
warnings=[] motion_enabled=True
```

3. `ros_tool_executor.py ... --mode execute --ledger ...`
   executed the two-step delivery plan:

```
# step 1: perception.verify_operator     status: succeeded
# step 2: mission-control.deliver_tool   status: succeeded
harness_exit=0
```

4. Mission node log tail:

```
[PLAN_PATH] 82% ... [NAVIGATE] 83% ... [LOCALIZING] ... [MOVING] ...
[ARRIVED] 94% Arrived at station_a.
[PLACE_AND_HANDOVER] 98% Tool placed in the handover area.
[COMPLETED] 100% Tool delivered and handover confirmed.
```

5. Ledger rows (the Phase 3 evidence):

```
perception.verify_operator:     completed goal_uuid=e86c3cdbb3854988b1cedffa2c1075f5 failure=None
mission-control.deliver_tool:   completed goal_uuid=1714300f840748358266073ecb6bd079 failure=None
commands=2
```

## What this proves

- The workspace builds on ROS 2 Humble outside the CI container.
- `ToolClient` 鈫?`RosBackend` 鈫?action clients work against real action servers,
  and receipts come back `completed`.
- **goal uuid 鈫?command id correlation works** (`backend_ref` was `NOT RUN` in
  the Phase 3 report; it is now measured).
- Submissions are durable before dispatch and terminal receipts are written,
  on a real run.

## What this does not prove

- **No hardware, no Gazebo.** Every backend here is the simulated one shipped in
  the launch file. Nav2, camera, arm and face recognition were not exercised.
- **No multi-threaded adapter.** The Phase 3.1 work (MultiThreadedExecutor,
  per-resource callback groups) is still outstanding.
- **No fault injection on the wire.** Dropped ACKs and restarts were simulated
  against the fake backend, not by killing DDS.
- **Timing is not representative.** Simulated steps run at 0.2鈥?.5 s each; a
  real navigation goal takes orders of magnitude longer.

## Repetition

`scripts/smoke_ros2_runtime.sh [ledger-path]` reproduces the whole sequence and
exits non-zero if the plan fails. It cleans up its own node stack.
