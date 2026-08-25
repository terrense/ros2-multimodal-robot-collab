# EXPERIMENT_LOG — 具身 Agent SFT→DPO→Step-Level PPO

> 规范:所有实验只汇总在本文件;每条记录含日期、配置、命令、结果、结论。全案规划见
> `C:\Users\Administrator\.claude\plans\projectheader-agent-sft-dpo-modular-pie.md`。

---

## 2026-08-25 | Phase 4:后训练栈落地(training/)+ 冻结 benchmark + 两处环境奖励缺陷修复

**内容**:新增 `training/`,把 agent_env 的闭环环境接成可训练的四段流水线。
- 单一 prompt 面 `chat_format.py`:数据生成/训练/评测共用一份 Observation 渲染与 Action 解析;对格式宽容(code fence、`<think>`、args 别名、扁平 args、尾逗号)、对内容严格(修复过的仍标 REPAIRED;格式正确但 skill 不存在算策略错误不算解析错误);无法解析的输出转成非法动作交给环境,记 grounding violation 而不是让 rollout 崩掉。
- 奖励与信用分配分离:`rewards/step_reward.py`(terminal/progress/grounding/safety/cost + 7 个消融预设)、`rewards/credit.py`(GAE over turns → per-turn advantage → action token span),并实现朴素 `terminal_broadcast` 作为可运行对照。
- 数据:SFT 语料;DPO 偏好对的方向**由环境验证**——每个 corruptor 提议都在克隆环境里真跑一遍,用 PPO 同一套奖励打分,只有确实更差的才入库(一次样本里 93 个 redundant_query、45 个 blind_retry 提议被判定"其实不更差"而丢弃);400 条冻结故障注入 benchmark,train/dev/benchmark 走不相交的种子块与 operator 池,scenario id 自带内容指纹可查泄漏。
- 策略与 rollout:规则基线(把部署系统的规则覆盖缺口显式参数化)、专家 oracle、vLLM/HF 后端、冻结 Teacher + 带预算的 turn gate;rollout 按 turn 批量并进,一次 generate 覆盖整批。
- 训练入口:SFT / DPO / Step-Level PPO / OPD,DeepSpeed ZeRO-3 + vLLM,OPD→PPO 交接写成配置里的 schedule(opd 1.0→0.1,env 0.2→1.0)。
- 评测:失败分类学(premature_stop / false_success / wrong_cancel / step_limit + 行为标记)、按故障族/失败阶段/轨迹长度分桶、report 把实测值与**预设验收目标**并排渲染,未跑的臂显示 pending。

**验收命令**:
```
python -m agent_env.scripts.collect_trajectories --acceptance --out data\l1_acceptance.jsonl
python -m training.data.build_benchmark --verify
python -m training.evaluate --policy rules  --label rule_baseline --out runs\eval\rule_baseline.json --markdown
python -m training.evaluate --policy expert --label expert_oracle --out runs\eval\expert_oracle.json
python -m pytest
```

**结果(400 条固定故障注入 benchmark,贪心)**:

| 策略 | recovery success | task success | premature stop | 停滞率 | 平均步数 |
|---|---|---|---|---|---|
| 规则基线(部署系统重建) | 76.1% | 63.0% | 21.5% | 9.5% | 10.5 |
| 脚本专家(每类故障都有规则) | 100% | 84.5% | 0% | 0% | 11.7 |

- benchmark 专家验证 6 桶全部 100% recovered、0 非法动作 → 基准可解,测的是策略不是环境
- L1 验收集 140 集仍全部 PASS
- 202 passed / 1 skipped

**评测抓到的两处缺陷(已修)**:
1. **正确的恢复动作被判为"无进展"**。第一版评测里 100% 恢复的专家有 33.8% episode 被标记停滞。根因:no-progress 计数基于状态指纹,而 `navigation.reposition`(遮挡场景的主要恢复动作)除清除遮挡标志外不改变任何可见状态。修复:把 `occlusion_cleared` / `low_conf_cleared` 纳入指纹。
2. **no-progress 阈值定在 2 会把故障的账算到策略头上**。注入故障时连续两步不推进完全可能是环境造成的(重试一次导航超时、重拍一帧低置信图)。实测专家 max 恒 ≤2,规则基线有 17% 达到 3 以上。阈值 2→3 后:专家停滞率 33.8%→0%,规则基线 44.8%→9.5%。
3. OPD 损失原按权重和归一化,导致 turn-level 权重只剩相对意义(全体权重翻倍等于没变),"student 越不确定权重越大"跨 episode 失效。改为按已打分 token 数归一化。此问题由单测发现。

**结论**:后训练所需的环境、数据、奖励、信用分配、rollout、评测六层已跑通并有测试覆盖;规则基线与 oracle 之间 24 个点的 recovery 差距(主要是 premature stop)构成明确的优化目标。第 1、2 条修复本身就是"手写 reward 会有 bias"的实例——也正是引入 OPD 的动机。

**遗留**:四个模型训练阶段尚未在 GPU 上执行(loss 与 experience 组装有 CPU 单测,分布式部分没有);`docs/results.md` 中 pending 的行即为待跑实验臂;gazebo_adapter.py 与 L1 转移概率的 Gazebo 实测校准仍未开始。

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
