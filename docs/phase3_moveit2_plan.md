# Phase 3 — 真机械臂(MoveIt2)实施文档

> 状态:**设计完成,待执行**。本阶段必须在 WSL + ROS2 Humble + Gazebo 环境下边写边验证,
> 不能盲提交(会有破坏 `colcon build` 的风险)。本文档把步骤拆到可直接执行。

## 目标
把 `arm_skill_server` 的 pick/place 从纯模拟接到 **MoveIt2** 规划执行:在 Gazebo 里真的有一条臂
动作,抓取点用 Phase 2 的 `ToolDetection.tool_pose`;**保留**现有手势暂停/急停通道和 `_async_sleep`
模式(见 memory `rclpy-async-sleep-gotcha`)。

## 关键决策(待你确认)

### D1. 选哪条臂?
| 选项 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| **A. Panda(franka_description)** | MoveIt2 官方示例最全,resource 多,配置成熟 | 7-DOF、较大,装在 0.56×0.38 小车上比例失真;依赖较多 | 学习/演示快但失真 |
| **B. 小型现成臂(如 OpenManipulator-X / xArm 描述)** | 体积匹配小车,4-5 DOF,贴近真实工作站 | 需引入对应 description 包 | **推荐**,比例真实 |
| **C. 自定义 4-DOF xacro** | 完全可控、最小依赖 | 要自己写 URDF + ros2_control + 调 IK 可解性,工作量大 | 仅在不想引第三方包时 |

> 默认建议 **B(OpenManipulator-X)**。请你回来时确认,或改选 A/C。

### D2. 控制后端
Gazebo Classic + `gazebo_ros2_control`(`libgazebo_ros2_control.so`)挂 `JointTrajectoryController`,
MoveIt2 通过 `FollowJointTrajectory` 控制。需要 `ros2_control` + `controller_manager`。

### D3. arm_skill_server 接 MoveIt 的方式
- 优先 **`moveit_py`**(Python 直接 plan+execute),代码最简洁;若 Humble 上 `moveit_py` 不稳,
  退化为调用 `/move_action`(MoveGroup action)。
- **懒加载**:像 YOLO 节点那样在执行路径里 `import`,这样没装 MoveIt 时节点仍能起(回退到现有
  模拟循环),**保住离线 baseline**。建议新增 `use_moveit` 参数,默认 `false`。

## 执行步骤(每步后在 WSL 验证再下一步)

### S1. 装依赖(WSL)
```bash
sudo apt install -y ros-humble-moveit ros-humble-gazebo-ros2-control \
  ros-humble-ros2-controllers ros-humble-controller-manager
# 选项 B:
sudo apt install -y ros-humble-open-manipulator-x-description   # 或对应 description 源码
```
对应在各 `package.xml` 增 `<exec_depend>`:`moveit_ros_planning_interface`、`gazebo_ros2_control`、
`controller_manager`、`joint_trajectory_controller`、所选臂的 description。

### S2. URDF:把臂挂到小车
- 在 `src/robot_collab_sim/urdf/collab_mobile_base.urdf.xacro` 里 `xacro:include` 臂的 xacro,
  通过固定关节把臂 base 接到 `base_link`(顶面,约 `xyz="0.1 0 0.08"`)。
- 加 `<ros2_control>` 段 + `gazebo_ros2_control` 插件,`<parameters>` 指向 S3 的 controllers yaml。
- **验证**:`colcon build` 通过;`ros2 launch ... gazebo_world.launch.py` 里 Gazebo 能加载带臂模型,
  `ros2 control list_controllers` 看到 joint_state_broadcaster + arm controller。

### S3. controllers 配置
- 新建 `src/robot_collab_bringup/config/arm_controllers.yaml`:`joint_state_broadcaster` +
  `arm_controller`(JointTrajectoryController)+ `gripper_controller`。

### S4. MoveIt2 配置包
- 用 `ros2 run moveit_setup_assistant moveit_setup_assistant`(GUI,WSLg)对带臂 URDF 生成
  `robot_collab_moveit_config`(SRDF、kinematics.yaml、joint_limits、moveit_controllers、
  `move_group.launch.py`);planning group 命名为 `arm` + `gripper`。
- **验证**:单独 `ros2 launch robot_collab_moveit_config demo.launch.py`,RViz 里能拖动规划。

### S5. 改写 arm_skill_server
- `arm_skill_server.py` 的模拟循环(现 `_execute` 里 CHECK_SCENE…DONE 七步)替换为:
  收到 `PickAndPlace` goal → 取 `goal` 里的目标位姿(来自 mission 传来的 `ToolDetection.tool_pose`)
  → MoveIt plan 到 approach/grasp → 控制 gripper → lift → 移到 target_frame → place。
- **保留**:`/arm/control_command` 的 pause/resume/`system_stop` 处理(现 line ~50-109),
  在每步之间检查 `_paused`/`_stopped`,急停时 `cancel` MoveIt 执行;沿用 `_async_sleep`。
- `use_moveit:=false` 时走旧模拟逻辑(离线可跑)。

### S6. launch 集成
- `gazebo_nav_vins_demo.launch.py` 增 `use_moveit` 开关(默认 false);为 true 时
  include MoveIt `move_group` + spawn controllers,并给 arm_skill_server 传 `use_moveit:=true`。
- 与 `FULL_NAV`/`use_yolo` 一样,真功能 opt-in,默认 baseline 不变。

## 验证(WSL,整体)
```bash
FULL_NAV=1 bash scripts/run_gazebo_visible_demo.sh use_yolo:=true use_moveit:=true
# 派任务,观察:YOLO 检出 → Nav2 开到取料点 → MoveIt 臂规划并移动到检测位姿 → 递送
# 手势急停:ros2 topic pub --once /arm/control_command ... command: 'system_stop' 能中断臂动作
```

## 风险点
- `moveit_py` 在 Humble 的可用性/API 变化 → 备选 `/move_action`。
- 小车基座移动时臂的 TF 与规划坐标系(用 `base_link` 还是 `map`)要一致,抓取位姿需正确变换。
- Gazebo Classic 与 `gazebo_ros2_control` 的兼容(Humble 推荐 Gazebo Classic 11)。
- 比例/可达性:臂太小够不到 shelf 上的物体 → 调安装高度或抓取点。

## 完成标志
- `colcon build` 全绿;`use_moveit:=true` 时 Gazebo 里臂真动并完成抓取;急停可中断;
- `use_moveit:=false` 时旧模拟链路仍可跑;
- 完成后勾选 README Roadmap 的 "接入真实 MoveIt2" 项。
