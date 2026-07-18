# EXPERIMENT_LOG — 具身 Agent SFT→DPO→Step-Level PPO

> 规范:所有实验只汇总在本文件;每条记录含日期、配置、命令、结果、结论。全案规划见
> `C:\Users\Administrator\.claude\plans\projectheader-agent-sft-dpo-modular-pie.md`。

---

## 2026-07-17 | Phase 0:agent_env 环境层建成 + L1 验收 PASS

**内容**:新增 `agent_env/`(闭环协议 protocol.py、9 类故障注入 faults.py、L1 文本级模拟器 l1_sim.py、scripted 专家 expert_policy.py、JSONL 轨迹协议 trajectory_logger.py、动作注册表 schemas/agent_skills.json,13 个 step-level 动作)。纯 stdlib,无 ROS2 依赖。

**验收命令**:
```
python -m agent_env.scripts.collect_trajectories --acceptance --out data\l1_acceptance.jsonl   # seed=20260717
python -m agent_env.scripts.collect_trajectories --mix --episodes 400 --out data\l1_mix_check.jsonl --seed 777
```

**结果**:
- 验收集 140 集(50 无故障 + 9 故障×10):全部桶 recovered=100%,illegal=0,safety violation=0 → PASS
- 混合压测 400 集(无:单:双 ≈ 3:5:2,含 10 种双故障组合):全部桶 recovered=100%
- 语义正确性:tool_missing → 全部 correct_abort(先 re-perceive+reposition 再上报);identity_mismatch → sensor_glitch 变体重试成功、真不匹配 correct_abort;user_cancel → 先 system_stop 再 terminate(cancelled)
- 平均步数:无故障 9.7 步;最难桶 grasp_failure+tool_drop 18.9 步(上限 30)

**结论**:L1 环境 + 专家策略达到 SFT 数据生产标准。专家在全部单/双故障下 100% 恢复,可以作为 SFT 的 teacher。
**遗留**:gazebo_adapter.py 与 Gazebo 上云(等租 CPU 实例);L1 转移概率需用 Gazebo 实测校准。

---

## 2026-07-17 | 仓库可用性验证 + 关键 bug 修复(WSL Ubuntu-22.04-External, ROS2 Humble)

**验证项**:
- dry-run 校验层:正例 2 个示例 plan 通过;负例(未知 skill `teleport`、缺 required args)均被拒且 exit=1 ✅
- 全仓库 49 个 py 文件语法编译零错误 ✅
- colcon build 10 包全过(需补装 `ros-humble-nav2-msgs`,nav_skill_server import 期硬依赖)✅

**发现关键 bug(修复前 execute 模式无法完成任何任务)**:
1. `asyncio.sleep` 用在 rclpy executor 驱动的 action 回调里 → `RuntimeError: no running event loop` → goal 被 abort。波及 4 文件 7 处:face_auth_node、arm_skill_server、nav_skill_server、mission_state_machine。复现:verify_operator 返回 `authorized=false, confidence=0.0`(默认值,即回调崩溃)。
   **修复**:各文件加 `rclpy_sleep()`(create_timer + rclpy Future),替换全部 asyncio.sleep。
2. `ros_tool_executor` 把 goal status≠4(如 ABORTED)标成 "finished" 继续执行后续步骤,与其声明的 fail-fast 不符。**修复**:非 SUCCEEDED 一律 "failed" 停止。

**修复后端到端**:demo_sim 9 节点全在线,executor execute 模式两步均 succeeded,状态机日志 VERIFY_IDENTITY→NAVIGATE→PICK_TOOL→PLACE_AND_HANDOVER→[COMPLETED] 100%,零 traceback ✅

**结论**:调度/校验/action 链路验证正常,可以放心作为 L2 训练环境基座。修复未提交 git,待 review。
**产出**:`deploy/docker/`(CPU 云镜像 Dockerfile + 并行采数脚本,依赖清单来自本次实测)。

---
