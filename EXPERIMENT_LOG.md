# EXPERIMENT_LOG — 具身 Agent SFT→DPO→Step-Level PPO

> 规范:所有实验只汇总在本文件;每条记录含日期、配置、命令、结果、结论。全案规划见
> `C:\Users\Administrator\.claude\plans\projectheader-agent-sft-dpo-modular-pie.md`。

---

## 2026-11-10 | L1↔L2 校准契约 + 报告头声明"仅 L1"

**内容**:L1 的故障参数是**配置出来的**,不是测出来的。`default_params` 说导航会超时
`timeouts_before_success` 次、低置信重拍有 0.5 的概率修好、人脸不匹配 35% 是抖动。
live stack 是否如此是经验问题,没回答之前这些数都是假设。

- **L1 参考值从配置本身推导**(`agent_env/l2_calibration.py`),不是手写:四类随机
  故障用 `default_params` 抽样估计(nav_timeout 0.665 ≈ 2/3、grasp_failure 0.659、
  low_confidence 0.500、identity_mismatch 0.346),四类确定性故障取自模拟器语义
  (换视角清遮挡、TTS 解阻塞、掉落可重捡、取消必停)。抽样的种子由族名派生,所以
  估计不会在两次运行之间漂。
- **每族的 L2 测量方案写成了数据**:要跑哪几步、"什么算成功"、需要先在 Gazebo 里
  摆好什么场景,都在 `PROGRAMMES` 里,可读可审。
- **清单**:`benchmarks/calibration_v1.json`,少于 30 个样本绝不写 calibrated,区间
  不覆盖 L1 值也绝不写。目前九族**全部 `not_measured`**——因为 L2 一次都还没跑,
  这就是事实。
- **报告头据此声明**:Phase 7 要求"未校准的族要在报告头点出来",这条之前没实现,
  现在 summary 块会带一行 `L2 status: 0/9 ... Every result below is L1 only.`

**验收命令**:
```bash
python -m agent_env.scripts.calibrate_l2 --plan
python -m agent_env.scripts.calibrate_l2 --l1
python -m agent_env.scripts.calibrate_l2 --from-log runs/l2_measurements.json
python -m pytest tests/test_l2_calibration.py
```

**结果**:
- 14 个新测试(参考值推导与稳定性、<30 样本不校准、区间不覆盖不校准、清单读写、
  报告头的 L2 声明)。
- 361 passed / 3 skipped。

**结论**:L1 的故障模型现在有了**可被推翻的写法**——每族都写明了 live stack 必须
表现出什么行为才算数,以及这个行为在 L1 里是多少。没有跑过就是没跑过,报告会自己
说。

**遗留**:真正跑 L2 还需要两样东西——(1) `agent_env/l2_gazebo.py`:把 live stack 包
成 `reset()/step()` 环境的适配器,现在只有 `ros_tool_executor.py` 逐条派发;(2) 在
Gazebo 车间里为每族摆出故障的可复现场景(遮挡、掉落、阻塞交接)。两样都不需要 GPU,
但都需要把仿真栈跑起来。Phase 6 任务表已按实际情况更新。

---

## 2026-11-10 | 显存配置修复:rollout 预算预检 + teacher 独立服务

**内容**:把上一条记录里定位的两个 OOM 修掉,并让"下次还会不会 OOM"变成启动时的
一次检查,而不是跑一小时之后的崩溃。

- **rollout 预算**:`training/memory.py` 新增显存账——ZeRO-3 分片、**未切分的参考
  模型**、vLLM 自己那份权重副本、KV cache 需求,按训练循环的真实布局逐项算。四份
  rollout 配置的 `gpu_memory_utilization` 从 0.55 / 0.45 统一降到 **0.38**
  (96 GB 卡上是 36.5 GB = 权重 16.4 + KV cache 20.1)。留 0.38 而不是 0.42 是因为
  余量还得盖住**训练步的激活峰值**——那一刻引擎还占着它那份。
- **启动预检**:`PPORunner.preflight_rollout_memory` 在引擎起来之前用
  `torch.cuda.mem_get_info()` 量真实剩余显存,装不下就带着"改成多少能装下"的错误
  信息退出;同时打印这张卡的 KV cache 能同时放下多少条全长序列(并发 32 在 8192
  上限下是超订的)。
- **teacher 独立服务**:teacher 是 30B(60.8 GB bf16),**每个 rank 各加载一份就是
  4 × 60.8 GB**,没有任何摆法放得下。改为 `teacher.backend: server`——teacher 自己
  起一个 vLLM 服务(`training/scripts/serve_teacher.sh`),训练进程用 HTTP 问它要
  log-probability。请求带 **token id 而不是文本**,teacher 的 tokenizer 不可能把
  student 的 token 重新切一遍;返回的 log-prob 转成与进程内路径完全一致的对齐。
  `backend: local` 保留给单进程调试,多 rank 时直接报错并指向服务后端。

**验收命令**:
```bash
python -m pytest tests/test_memory_budget.py tests/test_remote_teacher.py tests/test_training_memory_config.py
python -m pytest
```

**结果**:
- 26 个新测试:显存账、预检、teacher 客户端(用假 HTTP 服务端)、以及"四份配置
  在 96 GB 卡上必须装得下"的回归测试。
- 347 passed / 3 skipped。

**结论**:两条 OOM 路径在跑之前被堵住了,而且不是靠注释——配置值本身被测试钉住,
摆法不对时是明确报错而不是崩溃。

**遗留**:预检量的是"装得下",不是"跑得快"。KV cache 在 8192 上限下只放得下 16 条
全长序列;实测 episode p95 约 5k token 时是 27 条、p50 约 3.5k token 时是 38 条,
够 32 并发用,但 episode 变长就要降 `rollout.concurrency`。第一次 GPU run 之后要
把 `torch.cuda.max_memory_allocated()` 写进 metrics,用实测替换这里的算术。下一个
杠杆是那 16.4 GB 的参考模型——它不参与 ZeRO-3 切分,每卡一整份,是最容易省掉的
一块(见 post_training.md §15 第 5 条)。

---

## 2026-11-10 | 实验基线复现 + results.md 生成机制收尾 + 显存预算

**内容**:把"实测数字"和"生成机制"两件事补齐,让 `docs/results.md` 从一份手抄的
文档变成可复现、可校验、且不会悄悄丢数字的文档。

- **基线复现**:在 L1 模拟器上重跑两条参考策略(纯 CPU,无需 GPU)。
  规则基线 recovery_success **76.1%** / task_success 63.0% / premature_stop 21.5% /
  停滞率 9.5% / 平均 10.5 步;脚本专家 recovery **100%** / task_success 84.5%。
  与 2026-08-25 的记录**逐位一致**,说明那条链路上没有采样、不依赖随机种子。
- **逐故障拆解**:基线 86 次 premature_stop **全部**落在 `tool_drop`(48)与
  `no_progress`(38)两类,即 `RuleCoverage` 里关掉的两条规则;nav_timeout /
  visual_occlusion / low_confidence / grasp_failure 无论单发还是组合都是 100% 恢复。
  23.9 个百分点的差距不是弥散的,是两个具体缺口。
- **生成机制**:`training/report.py` 改为块级生成,`docs/results.md` 里
  `<!-- BEGIN GENERATED: x -->` 之间由脚本拥有,块外的手写正文原样保留。
  新增 `--update`(原地刷新)与 `--verify`(CI 校验,陈旧即非零退出)。
- **证据门禁**:新增 `training/run_record.py` 命令行,运行记录直接对**活的** prompt
  面与 action schema 取哈希;没有运行记录的臂在表里显示 `unverified` 而不是数字。
  评测产物与运行记录入库到 `results/`,干净 checkout 即可无 GPU 重建整张表。
- **失败关闭**:回写时若某个已发布的数值会退化成 pending/unverified,直接拒绝并报错,
  不允许"因为本机没有产物"就把已测数字抹掉(可用 `--force` 显式覆盖)。
- **显存预算**:从配置推出每条臂的显存账,并定位两处装不下的地方(见下)。

**验收命令**:
```bash
python -m training.evaluate --policy rules  --label rule_baseline --out runs/eval/rule_baseline.json --markdown
python -m training.evaluate --policy expert --label expert_oracle --out runs/eval/expert_oracle.json --markdown
python -m training.run_record --name rule_baseline --kind eval --model-id rule_baseline \
    --policy rules --episodes 400 --artifact results/eval/rule_baseline.json \
    --out results/run_records/rule_baseline.json
python -m training.report --update docs/results.md
python -m training.report --verify docs/results.md
python -m pytest
```

**结果**:
- 规则基线 76.1% / 专家 100%,与上次记录逐位一致(400 条,360 条注入故障)。
- `python -m training.report --verify docs/results.md` 通过 —— 库里的表就是生成器会写出的表。
- 321 passed / 3 skipped。

**显存(按 Qwen3-8B 8.2B 参数估,bf16 权重 16.4 GB;未实测)**:
| 阶段 | 单卡常驻(估) | 建议 |
|---|---|---|
| SFT | 25–33 GB(ZeRO-3 4 卡切分) | 4×48 GB;4×24 GB 需 `zero3_offload.json` |
| DPO | SFT + 16.4 GB 参考模型 | 4×48 GB 以上 |
| PPO | 33 + 16.4 + vLLM 0.55×卡显存 | 4×80–96 GB,需把 `gpu_memory_utilization` 降到 0.33–0.35 |
| OPD | PPO + 60.8 GB teacher | 4×96 GB,teacher 必须跨卡摊开或独立服务 |

**结论**:后训练栈的"数字"这一侧现在是可信的——每个被引用的数值都有产物、有运行
记录、有哈希、且能从干净 checkout 重建;剩下的六条臂只差 GPU。

**遗留**:两处显存问题必须在第一次 GPU run 之前修掉,否则一定 OOM:
(1) PPO 的参考模型在 DeepSpeed engine 之外,每卡一整份 16.4 GB,加上 vLLM 常驻,
96 GB 卡按当前 0.55 算约 107 GB 装不下;(2) OPD 的 `TeacherScorer` 用
`device_map="auto"` 加载 30B teacher,accelerate 会先把 60.8 GB 塞进 0 号卡,
与 ZeRO-3 分片叠加必然 OOM——需要显式 `max_memory` 摊开,或把 teacher 做成独立
vLLM 服务。真跑起来后要把 `torch.cuda.max_memory_allocated()` 写进 metrics,
用实测替换这里的算术。

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
