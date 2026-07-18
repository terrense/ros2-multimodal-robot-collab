# CPU 云实例部署(headless 仿真 / L2 数据采集)

## 镜像构建

```bash
# 国内云(默认走清华源):
docker build -t robot-collab:latest -f deploy/docker/Dockerfile .
# 实例自带加速源(如 AutoDL)时:
docker build -t robot-collab:latest -f deploy/docker/Dockerfile --build-arg USE_TUNA=0 .
```

基础镜像 `ros:humble-ros-base-jammy` + 实测依赖(2026-07-17 在 WSL Ubuntu-22.04 + Humble
上验证过完整递送任务 COMPLETED):`nav2-msgs`(nav_skill_server 的 import 期硬依赖,
即使 simulated 后端也需要)、`gazebo-ros-pkgs`(gzserver headless)、`navigation2` +
`nav2-bringup`、`slam-toolbox`。

## 冒烟测试(进容器后)

```bash
# 1) 无 Gazebo 的全节点 demo + 真实 ROS2 调度(与本地验证相同的用例)
ros2 launch robot_collab_bringup demo_sim.launch.py &
sleep 8
python3 agent_harness/scripts/ros_tool_executor.py \
  agent_harness/examples/deliver_hex_key_plan.json --mode execute --timeout-sec 120
# 预期: 两步均 status: succeeded,mission 日志走到 [COMPLETED] 100%

# 2) headless Gazebo(L2 世界)
ros2 launch robot_collab_sim gazebo_world.launch.py gui:=false
```

## 多实例并行采数

单机多容器,按 `ROS_DOMAIN_ID` + `GAZEBO_MASTER_URI` 隔离:

```bash
./deploy/docker/run_parallel_sim.sh robot-collab:latest 6
```

32 核实例建议 4–6 个 gzserver 实例起步,观察 CPU 饱和度再调。
轨迹统一写入挂载的 `out/`(JSONL 协议见 `agent_env/trajectory_logger.py`)。

## 云上流程

1. 租 32 核 CPU 实例(约 1–2 元/时),装 Docker
2. `git clone` 本仓库(或 `docker save/load` 传镜像)→ 构建 → 冒烟测试
3. `run_parallel_sim.sh` 批量跑 L2 采数(gazebo_adapter 完成后接入 agent_env 同一套 env.step() 接口)
