# 这是什么，仿真在干什么，OpenRUA 融合到哪了

一页讲完。给两类读者：**想快速判断这个项目值不值得看的人**，和**想接着往下做的人**。

---

## 1. 这个仓库在回答一个问题

> **哪种监督信号，能修好哪种失败模式？**

工具递送任务在真实世界里会遇到这些东西：导航超时、工具被遮挡、抓取滑脱、转运途中掉件、
操作者不在工位。每一种都可以手写一条 recovery rule，但规则条数的增长速度比覆盖率的
增长快得多。所以真正的问题不是「怎么写规则」，而是：

- 如果只用**规则**（部署系统的重建）当监督信号，策略会学成什么样？
- 如果换成**示范数据**（SFT）、**成对偏好**（DPO）、**过程奖励**（Step-Level PPO）、
  **教师蒸馏**（OPD），**每一种各自修好了哪一类失败**？

这个仓库的产物就是这张对照表，以及支撑这张表可信的证据链。

## 2. 仿真在这件事里干什么

**仿真不是为了「看起来像机器人」，是为了让故障可控、可重复、可量化。**

真实的工位偶发一次导航超时，样本量根本不够，结论也不可信 —— 你没法在两种训练方法之间
做比较，因为两次实验的故障分布不一样。仿真里同一批场景可以反复注入同一批故障：

- **冻结的 400 条故障注入 benchmark**（`benchmarks/fault_injection_v1.jsonl`）：40 条干净 +
  360 条带故障，覆盖 9 类故障和它们的组合，训练/评测严格隔离。
- **可注入的故障**：导航超时、视觉遮挡、低置信检测、工具缺失、抓取失败、掉件、人脸不匹配、
  用户取消、无进展（L1 模拟器 `agent_env/`）。
- **可测的证据**：每个动作的回执、执行账本、失败分类学（premature stop / false success /
  wrong cancel / step limit）、按故障族和失败阶段分桶。

**仿真的边界说清楚**：没有真机验证。机械臂执行是仿真的执行器（`arm_skill_server` 用
`simulate_step_seconds` 模拟），人脸/语音/手势是桩或 vendored 源码，SLAM 用 odometry 建图
（scan matching 关着，因为调参到 84% 跟踪率仍然导航失败）。所以本仓库里的一切结论都是
**「L1 仿真里」**的结论，`docs/results.md` 的报告头会一直声明这一点，直到真栈的校准数字
把它们改掉。

### 两个场景，一个仿真

早期有两个 Gazebo world，读者常以为是两套东西：

| World | 内容 | 谁在用 |
|---|---|---|
| `collab_lab.world` | 地面、站点标记、工具架、箱子和墙（障碍物） | **默认**。`demo_sim.launch.py` 用的就是它 |
| `workshop.world` | 墙、工作台、棋盘标定板 | `--workshop` 时加载，三台官方机械臂（KUKA KR10 / FANUC M-10iA / Dobot CR10）随后 spawn 进来 |

它们**不是两个仿真**：同一个 launch、同一套 ROS 节点、同一张图、同一套运行时。
区别只有「加载哪个 world 文件」和「要不要把三台官方机械臂 spawn 进来」。
移动小车在两个 world 里都在（`gazebo_world.launch.py` 负责 spawn 它）。

而**三台机械臂需要额外下载**（KUKA 和 Dobot 的官方描述带网格，几百 MB，在
`third_party/robot_descriptions/`，不入库；FANUC 走 apt 包）。所以第一次跑车间场景要先
`bash scripts/fetch_robot_descriptions.sh`。

## 3. OpenRUA 融合到哪了

上游 `Open-RUA/uni-robot-agent` **没有任何训练代码** —— 它是 agent 编排框架，不是 RL 框架。
这个仓库吸收的是它的**工程纪律**：契约化、可靠执行、每个副作用都有证据。

吸收进来的是八个阶段（`docs/plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`）：

| 阶段 | 吸收了什么 | 在哪 |
|---|---|---|
| O0 | 两仓库审计：上游 22 条机制逐条判 ADAPT / ADAPT / REFERENCE / REJECT | `docs/openrua_audit/` |
| O1 | 版本化契约：命令身份、执行状态（含 `unknown`）、回执、快照、能力注册表 | `agent_harness/contracts/` |
| O2 | 可靠运行时：每资源 FIFO、只撤销排队、失败关闭 | `agent_harness/runtime/` |
| O3 | 执行账本 + ROS 2 适配器 + DDS 丢包注入 | `agent_harness/runtime/ledger.py`, `wire_faults.py` |
| O4 | 不可变观测快照 + 新鲜度门 + 能力发现 | `agent_harness/runtime/snapshots.py` |
| O5 | Agent 网关：observe → 决定一个动作 → 校验 → 提交 → 等回执 | `agent_harness/agent/` |
| O6 | 评测对齐：L1 回执、Wilson 区间校准、回执指标、等预算消融 | `training/`, `agent_env/l2_calibration.py` |
| O7 | 训练集成：溯源、运行记录、生成式结果、奖励作弊审计 | `training/provenance.py` 等 |

**任务清单全勾了，但那不等于活干完了** —— 这正是审计存在的理由
（[`docs/specs/completion-audit.md`](specs/completion-audit.md)）：八个阶段的任务文件
当时每一条都是勾上的，而**计划自己要求的三样东西根本没做**：模型被中断这一条结果、
七个具名基线里的五个、L1 路径的回执指标。三样现在都补上了，而且补的方式是能被检查的。

**还没融合完的部分**，按重要性：

1. **四个故障族这栈造不出来**（`visual_occlusion` / `tool_drop` / `no_progress` /
   `grasp_failure`）。这不是布景问题，是软件问题：感知桩不观察世界、没有东西会放下手里的
   工具、没人查交接区占用、仿真臂不会抓空。探针会说明缺哪个机制。
2. **六条训练臂一条没跑** —— 缺 GPU，其余都通了。

## 4. 现在测出了什么

- **校准**：9 族故障里 5 族在真栈上实测（每族 30 样本），**4 族推翻 L1**。方向一致：
  L1 假设了真栈没有的自愈能力。[图](../docs/figures/calibration.svg)
- **线缆故障**：6 套场景 × 12 条判据全过，95% 丢包下命令以 `unknown` 收场（不是 `failed`），
  账本挡住后续派发直到人工认领 —— fail-closed 那条契约第一次被真正走到。
  [图](../docs/figures/wire_faults.svg)
- **回执**：给闭环带来 **+80 个百分点**的任务成功率（3.3% → 83.3%），同一策略只差一个变量。
  [图](../docs/figures/conditions.svg)
- **CI**：绿。ROS job 末尾跑三件需要真仿真的事：一次真实递送、一次校准探针、一次线缆故障。

## 5. 接着往下做

| 想做什么 | 从哪开始 |
|---|---|
| 跑六条训练臂 | [`docs/post_training.md`](post_training.md) §15 有显存预算（含两处必须改的配置） |
| 补那四个造不出的故障族 | 要改机器人自己的节点；先看一眼 §「两个决定」里的第二个问题 |
| 把 L1 的故障模型改成和真栈一致 | [`docs/status.md`](status.md) 的「两个决定等你拍板」第一节 |
| 看某条线的细节 | [`docs/specs/README.md`](specs/README.md)（阶段地图）、[`docs/status.md`](status.md)（现状与卡点）、[`docs/roadmap.md`](roadmap.md)（总览） |
| 复现任何一张图 | `python3 scripts/make_figures.py`，数字会重新对上 `docs/figures/figures.json` |
