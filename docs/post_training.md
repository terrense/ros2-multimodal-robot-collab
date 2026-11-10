# 具身 Agent 后训练：设计取舍

这份文档解释 `training/` 为什么长成现在这样。工程说明见
[`training/README.md`](../training/README.md)，环境层见
[`agent_env/README.md`](../agent_env/README.md)，实测结果见
[`results.md`](results.md)。

---

## 1. 这个项目为什么需要后训练

原系统不是"给机器人套一个大模型"。底层导航、检测、抓取全部由确定性模块完成，
LLM 只负责高层任务规划。在标准化工作站里，这套 "LLM Planner + ROS2 状态机"
已经能稳定跑完闭环，所以只测标准任务根本说明不了后训练有什么意义。

问题出在 environment disturbance：Nav2 中途超时；工具被遮挡没检测到；第一次
grasp verification 返回 false；工具在转运途中掉了；操作者不在工作站；模型连续
三次调用同一个 Skill 而环境状态毫无推进。每一种异常都可以写一条 recovery rule，
但组合数增长比覆盖率快，规则树很快就维护不动，而且在没见过的 error combination
上泛化很差。

这一点在仓库里是可量化的，不是一句判断。`RuleBaselinePolicy` 按"给预期内的单
故障写了规则、其余没写"重建了部署系统的行为，在 400 条固定故障注入 benchmark 上：

| 策略 | recovery success | task success | premature stop | 停滞率 |
|---|---|---|---|---|
| 规则基线（部署系统重建） | 76.1% | 63.0% | 21.5% | 9.5% |
| 脚本专家（每类故障都有规则） | 100% | 84.5% | 0% | 0% |

差的 24 个点里，绝大部分是 `premature_stop`——在还能救的局面下报失败退出。
这正是后训练要解决的问题。

所以研究目标不是学习底层 motor control，而是：

> 当高层 action 没有产生预期 effect 时，Agent 下一步应该怎么恢复。

本质上是一个 multi-turn sequential decision problem。

---

## 2. 为什么是 Skill-level，而不是让 LLM 输出连续动作

LLM 输出的是离散高层 Skill——navigate、detect、reobserve、reposition、grasp、
verify、handover、terminate——Nav2、YOLO、机械臂 controller 仍然负责真正执行。

两个原因：

**安全性。** 如果让语言模型直接输出速度或 joint angle，任何 hallucination 都会
变成物理风险。现在 LLM 的输出先过 JSON Schema 与 Skill Registry 校验，
`risk` / `requires_confirmation` 元数据拦截高风险操作，非法动作在进入 ROS2
之前就被拒绝。

**可训练性。** 一条高层 trajectory 只有 8–15 个决策 turn，而不是几千个
motor-control timestep。8B 全参数后训练在 4×H20 上才有现实可行性。

代价是显式的：高层策略无法修复底层执行的问题。如果 Nav2 的局部规划器本身不行，
再好的恢复决策也救不了——那是另一个层次的工作。

---

## 3. Environment 里真正的 State / Action / Observation

对 LLM 来说，机器人不是 Gazebo 的 raw state。每个 Skill 执行完，Environment 把
关键状态压缩成结构化 Observation：

```python
observation = {
    "step_index": 6,
    "task": {"tool_id": "screwdriver", "target_station": "station_a", ...},
    "robot_location": "tool_shelf",
    "holding_tool": None,
    "operator_verified": True,
    "detections": [],
    "last_action": {"skill": "perception", "action": "request_tool_detection", ...},
    "last_result": {"status": "failed", "error_code": "TARGET_NOT_FOUND", ...},
    "safety_events": [],
    "no_progress_count": 1,
    "steps_remaining": 24,
}
```

模型输出恰好一个 JSON action：

```python
{"skill": "navigation", "action": "reposition",
 "arguments": {"strategy": "viewpoint_left"}, "reason": "..."}
```

整条轨迹是 State → Action → Environment → Observation → State → …，PPO 和 OPD
共用同一个交互环境。

**这里有一条工程纪律**：渲染 Observation、解析 Action 的代码只有一份
(`training/chat_format.py`)。数据生成、训练、评测都走它，所以 prompt 一改，三边
同时改，不会出现训练分布和评测分布悄悄分家的情况。

解析对**格式**宽容（code fence、Qwen3 的 `<think>` 块、`arguments` 别名、扁平
args、尾逗号都能修复），对**内容**严格：修复过的解析仍然标记为 `REPAIRED`，
格式正确但调用了不存在的 skill 仍然是策略错误而不是解析错误。这样评测才能把
"输出了乱码"和"选择了非法动作"分开统计。无法解析的输出会变成一个非法动作交给
环境，记一次 grounding violation——模型拿到学习信号，而不是让 rollout 进程崩掉。

---

## 4. 数据是怎么来的

不是凭空合成一万条机器人问答。

先用状态机和人工策略生成标准专家轨迹，再在环境里主动做 fault injection：
navigation timeout、target missing、visual occlusion、grasp failure、tool drop、
identity mismatch、no progress、user cancel，以及 10 组兼容的双故障组合。这些
轨迹构成 SFT 语料，只保留环境判定为 recovered、零非法动作、零安全违规的 episode。

DPO 的偏好对做了一处和常规做法不同的处理：**偏好方向由环境验证，不由人断言。**

`training/data/corruptors.py` 里的每条规则只负责*提议*一个"看起来合理但更差"的
替代动作——blind retry、premature failure、false success、跳过确认、未授权移动、
凭空编一个站点、用过期的 grasp_id、无视 cancel。`build_dpo.py` 把每个提议放进
克隆的环境里**真跑一遍**，用 PPO 之后要优化的同一套复合奖励给两个分支打分，
只有确实更差（差距超过 margin）的才成为 rejected。

这一步不是形式主义。实测中大量提议被判定为"其实不更差"而丢弃——一次样本里
93 个 `redundant_query`、45 个 `blind_retry` 提议没有通过验证。手写的"坏动作"
经常并不坏：多查一次状态只是浪费一步，重新检测一次可能本来就是对的。如果不
验证就当作 rejected，DPO 学到的方向会和 PPO 优化的方向不一致。

trajectory 粒度的对能直接生成叙述里那个例子：

```
chosen:   抓取失败 → 重新检测 → 重新规划抓取位姿 → 再抓 → 验证
rejected: 抓取失败 → 用同一个过期 grasp_id 重试 ×4
```

两条都不违法，一条浪费四步，reward 差距 2.14。

---

## 5. SFT 修什么，DPO 修什么

**SFT** 解决的是"模型不知道怎么跟这个 Environment 交互"：Skill Schema 不合法、
Arguments 格式错误、看不懂 ROS2 Error Code、不知道某个 failure 之后有哪些合法的
Recovery Skills。它建立的是 behavior prior。

Loss 只算在 assistant span 上。Observation 是环境永远会提供的输入，不是需要模型
复现的目标——`training/tokenization.py` 通过重复套用 chat template 并对比前缀
长度来定位每个 assistant span，如果模板不是前缀稳定的就直接报错，而不是让
loss mask 悄悄错位一个 token。

**DPO** 解决 SFT 表达不了的排序问题：两条轨迹都完成任务，一条 8 步，另一条
三次错误重试后 14 步；普通 SFT 说不出哪条更好。DPO 学的是

- success & efficient > success but redundant
- safe recovery > risky recovery
- reobserve / replan > repeated blind retry

到这里仍然是 offline alignment：只能学我们给过的数据。

---

## 6. 为什么进入 PPO

机器人环境里 disturbance 的组合几乎无限，真正 online rollout 之后会遇到训练集里
没有的 state。

而且环境能提供一批非常可靠的 reward，比 LLM Judge grounded 得多：任务是否完成、
导航是否成功、目标是否检测到、抓取是否验证通过、有没有调用危险动作、用了多少步。

所以 PPO 是这个项目真正的 environment optimizer。

---

## 7. 为什么 Terminal PPO 不够，以及一个必须讲清楚的区别

一条 12 步的轨迹，第 4 步选了错误的恢复动作：

```
navigate → detect → grasp → [错误的恢复] → … → final failure
```

如果只有最后一个 reward 0，前面十几个决策很难知道错在哪。这就是 long-horizon
credit assignment 问题。

第一步改进不是 OPD，而是 Progress Reward：导航成功、找到目标、抓取成功、验证
通过都产生局部信号；连续重复、状态无推进则有惩罚。

**但 Process Reward 不等于 Credit Assignment。**

它只是让 reward 更密集。真正的 credit assignment 还需要把不同时间点的 reward 经
Return-to-Go / GAE 转成 advantage，再映射到生成对应 action 的 token span 上：

```
environment step reward → GAE over turns → per-turn advantage → action token span
```

这两件事经常被混为一谈，所以仓库里把朴素做法（把一个终局标量平摊到每个生成
token）也实现了一份 `terminal_broadcast`，作为可运行的对照而不是文字描述：

```bash
python -m training.train_ppo --config training/configs/ppo_progress.yaml \
  --set credit.terminal_broadcast=true --set run.output_dir=runs/ppo_progress_flatcredit
```

同样的奖励、同样的 rollout，只有归因方式不同——这是唯一能把"奖励更密"和"归因
更准"分开的实验。

### 一个实测到的奖励误设

评测第一版跑出来的结果是：100% 恢复的脚本专家，有 33.8% 的 episode 被标记为
"停滞"。原因是环境的 no-progress 计数基于状态指纹，而 `navigation.reposition`
——遮挡场景的主要恢复动作——除了清除遮挡标志之外不改变任何可见状态，于是
**正确的恢复动作被判定为没有进展**。

修了两处：把 `occlusion_cleared` / `low_conf_cleared` 纳入状态指纹；把 no-progress
的阈值从 2 提到 3。在注入故障的前提下，连续两步没有推进完全可能是环境造成的
（重试一次导航超时、重拍一帧低置信度图像）；实测专家从不超过 2，而规则基线有
17% 的 episode 达到 3 以上。阈值 2 会把故障的账算到策略头上。

这件事本身就是"手写 reward 会有 bias"的一个实例——也正是下一节的动机。

---

## 8. 为什么有了 Progress Reward 还需要 OPD

Progress Reward 解决 sparse feedback，但引入另一个问题：**reward 是人为设计的。**

"重新扫描"短期看 progress = 0，实际上是必要的恢复动作；一个写歪的 reward
function 反而会惩罚它——上面那个 bug 就是活的例子，而且它是被评测抓到的，不是
被想到的。

于是第二个研究问题：有没有一种既不需要为每种 recovery state 手写 reward、又比
终局成功/失败更 dense 的监督？

这就是 On-Policy Distillation。

---

## 9. OPD 在这个项目里具体做什么

Student 始终是 Qwen3-8B，自己在环境里 rollout。当它走到

> navigation succeeded，target not found，已经 search 两次

这样的 state 时，冻结的 Teacher 接收**完全相同的** task、history、observation 和
Skill Schema，对 Student **实际采样出来的 action token** 计算 log-probability。
如果 Student 对 `retry_detection` 很自信而 Teacher 更倾向 `reposition_and_rescan`，
这里就存在一个较大的 teacher-student disagreement。

与 SFT 最大的不同是：**Teacher 没有把自己的轨迹喂给 Student。** 状态仍然来自
Student 自己真正访问到的 state distribution。所以它训练的是

> 当我自己已经走歪到这个地方之后，下一步应该怎么办。

损失用 k3 估计的 reverse KL：`exp(δ) − δ − 1`，其中 `δ = teacher − student`。
非负、低方差、对 student 的 log-prob 可导，因此不需要额外的 policy-gradient 项。

一个实现细节值得说明：权重按**已打分 token 数**归一化，而不是按权重和归一化。
后者会让 turn-level 权重只剩相对意义——所有权重同时翻倍等于没变，"student 越
不确定权重越大"跨 episode 就失效了。这个问题是被单测抓出来的。

---

## 10. 为什么不是整条 trajectory 都调用 Teacher

太贵，而且没必要。正常的 navigation success、routine observation、已经学会的
简单动作，让 30B Teacher 重复打分没有 learning value。

只有这些 turn 才开门：error_code 非空、上一个动作 failed/rejected、出现 safety
event、连续 no-progress、Student action entropy 较高、上一次打分 disagreement 较大，
外加一个小比例的随机采样（保留一个无偏切片用于校准）。还有一个 episode 级的
预算上限。

需要诚实说明这个门省的是什么：在 `turn` 序列模式下（每个 turn 单独成序列），
跳过一个 turn 就是省下一次 teacher forward；在 `episode` 模式下一次 forward 覆盖
整条 episode，门主要起的是**集中信号**的作用，只有整条 episode 一个 turn 都没被
选中时才真正省下一次前向。

---

## 11. 为什么 Teacher 用 Qwen3-30B-A3B

**同 family。** Student 和 Teacher 的 tokenizer、tool-use style、reasoning pattern
更接近，OPD 不容易退化成跨模型的格式蒸馏问题。同一套 token id 指向同一段文本，
Student 采样的 token 可以直接送给 Teacher 打分。

**MoE 结构。** 总参数约 30B，active parameters 只有约 3B，teacher forward 的成本
明显低于 dense 32B。

Teacher 只做 inference，不训练。这意味着 4×H20 上不需要让 8B Actor、Critic、
Reference 和 30B Teacher 永久同时驻留——训练按 phase 做 GPU 复用：student
rollout → teacher scoring → advantage 计算 → actor 更新。wall-clock 会增加，但不
需要夸张的卡数。

---

## 12. 为什么不是纯 OPD

Teacher 也会犯错。它看到的是 textual observation，并不知道物理仿真的真正结果。
它可能认为 "reposition 更合理"，而真实执行下来 retry_grasp 反而成功。

**Teacher preference ≠ Environment truth。** 纯 OPD 最终只能逼近 Teacher，真正的
task success 仍然必须由 Environment 定义。

所以 OPD 适合早期：Student 的 recovery 能力很弱时，Teacher 提供 dense signal，
迅速教会它"不要继续无脑 retry"。当 Student 已经学会大部分 recovery pattern 后，
继续强迫它模仿 Teacher 的收益递减，这时应当降低 teacher guidance、提高
environment reward。

概念上就是：**早期 Teacher 教什么是有效的恢复行为，后期 Environment 决定什么真
的有用。** 这是整个项目最核心的研究假设，所以交接过程是一条写在配置里、每步
记进日志的 schedule（opd 1.0 → 0.1，env 0.2 → 1.0，前 20% 迭代保持起始值），
而不是一个隐含的经验做法。

---

## 13. 实验矩阵

只有五条主线，不做算法动物园：

| 臂 | 问题 |
|---|---|
| SFT + DPO | offline 基线 |
| Terminal PPO | 只用终局 environment reward 能到哪 |
| Progress PPO | 加入 step reward + turn-level credit |
| Pure OPD | 只靠 dense teacher guidance 能到哪 |
| OPD warm-up → Progress PPO | teacher dense guidance + grounded environment optimization |

没有必要再叠 GRPO、DAPO、GSPO、PRM。这个项目值钱的是**监督来源对比**，不是算法
数量。

四组消融各自隔离一个设计决策：去掉 grounding 项、去掉 safety 项、去掉 cost 项、
以及把 turn-level credit 换成 flat credit。

### 验收口径

`training/configs/experiment_matrix.yaml` 里的 `target` 是**跑之前写下的预设验收
标准，不是结果**。`training/report.py` 把它和实测值并排渲染，标注 met / missed，
没跑过的臂显示 pending 而不是从表里消失。

设定依据：公开的 ATOD/OPD 在 Qwen3-4B / ALFWorld 上大致呈现 GRPO < OPD <
OPD+RL Hybrid，纯 OPD 相对纯 RL 约 +4pt，Hybrid 再 +4–5pt。我们的 Student 是已经
过了 SFT+DPO 的 8B，起点更强、headroom 更小，所以目标设得比直接搬这些 delta
更保守。

最重要的不是 84.3 还是 84.8，而是 **failure decomposition**：如果 OPD 显著减少
premature stop 和 invalid recovery，而 PPO 更明显提高最终 task success 和真实
skill executability，研究假设就得到了支持；如果两者改善的是同一类错误，那说明
它们是冗余的，这个结论同样有价值。

---

## 14. 当前进度

已经跑通并有测试覆盖的：环境层、数据管线、奖励与信用分配、rollout、评测与报告。
规则基线与专家 oracle 的 benchmark 数字是实测的（见上表和
[`results.md`](results.md)）。

尚未执行的：四个模型训练阶段。它们的 loss 函数与 experience 组装在 CPU 上有单测，
但分布式部分（DeepSpeed ZeRO-3 + vLLM rollout + 30B teacher 打分）还没有在 GPU 上
跑过。`docs/results.md` 里 pending 的行就是还没跑的臂。

---

## 15. 显存预算：每条实验臂要多少 GB 的卡

下面是**从配置算出来的估算，不是实测**——这套训练栈还没有在任何 GPU 上跑过。
以 Qwen3-8B（8.2B 参数）为准，bf16 权重 = 16.4 GB。

单个 8B 模型做全参数微调的静态开销：

| 项 | 大小 |
|---|---|
| bf16 权重 | 16.4 GB |
| bf16 梯度 | 16.4 GB |
| AdamW fp32 状态（m, v） | 65.6 GB |
| fp32 master 权重（若保留） | +32.8 GB |

不切分时合计 98–131 GB。按阶段展开：

| 阶段 | 单卡常驻（估算） | 建议配置 | 说明 |
|---|---|---|---|
| SFT | 25–33 GB（ZeRO-3 4 卡切分） | 4×48 GB 稳妥；4×24 GB 要配 `zero3_offload.json` | `use_lora: false`，全参数 |
| DPO | SFT + 16.4 GB 参考模型 | 4×48 GB 以上 | 参考模型是整份拷贝，不参与切分 |
| PPO（`ppo_terminal` / `ppo_progress`） | 33 GB 分片 + 16.4 GB 参考模型 + vLLM 0.38×卡显存 | 4×96 GB | 已按第 1 条改过，配置值 0.38 |
| OPD（`opd_pure` / `opd_warmup_ppo`） | student 同 PPO；teacher 独立成服务 | student 4×96 GB **+** teacher 2–4 张卡 | 见下面第 3 条 |

四个结论，都是读配置读出来的，不是猜的：

**1. PPO 的瓶颈不是训练权重，是 rollout 的 KV cache。**
32 并发 × 8192 上下文 × 36 层，KV cache 需要 16–39 GB 量级。再加上 ZeRO-3 分片
（约 33 GB）、**没有切分的参考模型**（`train_ppo.py` 里 `self.reference =
load_causal_lm(..., trainable=False)` 在 DeepSpeed engine 之外，每张卡一整份
16.4 GB）、以及 vLLM 自己那份权重副本（16.4 GB），在 96 GB 卡上按原来的
`gpu_memory_utilization: 0.55` 算是 33 + 16.4 + 52.8 + 5 ≈ **107 GB，装不下**。

现在已经改掉：四份 rollout 配置统一降到 **0.38**（96 GB 卡上是 36.5 GB = 权重
16.4 + KV cache 20.1）。而且不再靠人记这个数——`training/memory.py` 在训练启动时
用 `torch.cuda.mem_get_info()` 量真实的剩余显存，算出这张卡最多能分给引擎多少，
装不下就直接报错并把可用的数值写进错误信息：

```
the rollout engine does not fit next to the training state.
  rollout budget: card 96.0 GiB, training state 45.0 GiB, free 51.0 GiB; engine
  asks 0.55 (52.8 GiB) for weights 16.4 GiB + KV cache 36.4 GiB; maximum that
  fits is 0.53
  Fix: lower rollout.gpu_memory_utilization to 0.53 or below, or lower
  rollout.concurrency so the KV cache shrinks.
```

留出来的余量不只是给引擎的：**训练步的激活峰值**（logits 那一层）也发生在这
张卡上，而那时引擎还占着它那份。所以 0.38 而不是 0.42——0.42 只剩 5 GB 余量，
而 8192 上下文的 logits 峰值就有 5 GB（实测 episode 约 3.5k token 时是 1.2 GB）。

同一处还会打印"这张卡的 KV cache 能同时放下多少条全长序列"，因为并发 32 在
8192 的上下文下是超订的（20.1 GB 只放得下 16 条全长序列；实测 episode p95 约
5k token 时是 27 条、p50 约 3.5k token 时是 38 条，够用）。

注意 `VLLMPolicy` 一定会在进程内 `vllm.LLM(...)` 起一个引擎，没有连外部 server 的
后端——所以"rollout 单独占几张卡"的解法今天还做不了，能调的是显存分配比例。

**2. ZeRO-2 不是省钱选项。**
`zero2_bf16.json` 不切分梯度和权重，4 卡下每卡反而要 16.4（权重）+ 16.4（梯度）
+ 24.6（优化器与 master）≈ 57 GB，比 ZeRO-3 更贵。它只在"卡少、显存大"时才有
意义。

**3. OPD 的 teacher 现在是独立服务，不再跟 student 抢卡。**
原来的写法是 `TeacherScorer` 用 `device_map="auto"` 在训练进程里加载
`Qwen/Qwen3-30B-A3B`：accelerate 会先把 60.8 GB 塞进 0 号卡，和 ZeRO-3 分片叠加
后在 96 GB 卡上必然 OOM；就算用 `max_memory` 摊开也没用——**4 个 rank 各加载一
份 teacher 就是 4 × 60.8 GB**，谁也放不下。

所以 teacher 改成一个独立的 vLLM 服务，训练进程通过 HTTP 问它要 log-probability：

```bash
bash training/scripts/serve_teacher.sh Qwen/Qwen3-30B-A3B 8100 4   # 另开几张卡
# 配置里对应：
#   teacher:
#     backend: server
#     base_url: http://127.0.0.1:8100/v1
```

请求里带的是 **token id 而不是文本**，所以 teacher 的 tokenizer 不可能把 student
的 token 重新切一遍；返回的 log-probability 会转成和进程内路径完全一致的对齐
（`tests/test_remote_teacher.py` 用一个假的服务端把这两点都钉住了）。

代价是卡数：student 4×96 GB，teacher 再要 2–4 张（TP=2 的话 2×48 GB 就够，
权重 30.4 GB/卡）。第 11 节说的"按 phase 复用 GPU"要等到 teacher 能与 student
轮流占用同一批卡才成立，今天是两张卡组。
`teacher.backend: local` 保留给单进程调试，多 rank 时会直接报错并指向服务后端。

**4. 这些数字在跑之前都只是算术。**
真跑起来以后第一件事应该是把 `torch.cuda.max_memory_allocated()` 写进
`metrics.jsonl`，用实测替换估算——包括上面这条 0.38 的调整值。启动时的预检
（`training/memory.py`）已经会打印实际剩余显存，但那只是"装得下"的判据，不是
"跑得快"的判据。

**5. 下一个杠杆是参考模型那 16.4 GB。**
`self.reference = load_causal_lm(..., trainable=False)` 在 DeepSpeed engine 之外，
所以它**不参与 ZeRO-3 切分**，每张卡一整份——这是当前单卡常驻里最大的一块，
也是 PPO 能省下的最大一块。把冻结的参考模型也交给 ZeRO-3 切分（4.1 GB/卡），
或者把它并进 engine 当成一个不更新的模块，都能省下 12 GB/卡。没做，因为这两条
路都要改训练的数值路径，而且在没有 GPU 的机器上无法验证。
