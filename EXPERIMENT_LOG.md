# EXPERIMENT_LOG — 具身 Agent SFT→DPO→Step-Level PPO

> 规范:所有实验只汇总在本文件;每条记录含日期、配置、命令、结果、结论。全案规划见
> `C:\Users\Administrator\.claude\plans\projectheader-agent-sft-dpo-modular-pie.md`。
>
> 每条记录前缀标明它属于哪条线:**PT** = 后训练线(`agent_env/` + `training/`),
> **O** = OpenRUA 吸收线(契约 / 运行时 / 证据),**R** = 机器人本体线。三条线的编号
> 解释见 [`docs/specs/README.md`](docs/specs/README.md)。

---

## 2026-11-11 | **O6** 第一次对着真 Gazebo 栈跑校准，抓到三个真 bug

**内容**:之前所有"实时栈"相关的东西都只是逻辑测试。这次把 workshop 场景真起起来
(20 节点 / 45 话题)，跑运行时冒烟和校准探针，一次暴露三个缺陷。

**先说明:栈本身是好的。** 起的是 `--workshop`(官方 KUKA/FANUC/Dobot + D435 +
VLP-16),14 个节点全部启动:
```
mission_state_machine, nav_skill_server, arm_skill_server, tool_detector_node,
face_auth_node, gesture_recognizer_node, voice_gateway_node, agent_gateway_node,
vins_mono_bridge_node, gazebo, diff_drive, ...
```
`ros2` CLI 看不到图(WSL + CycloneDDS 的老问题),但 rclpy 看得到 —— 所以补了
`scripts/ros_graph.py`,把 CLI 该做的事用节点自己做:`nodes/topics/services/actions/
call/wait`。冒烟脚本改用它。

**运行时冒烟:通过。** 两条命令都 `completed`,goal uuid 与回执关联:
```
perception.verify_operator:     completed  goal_uuid=25a452d8...
mission-control.deliver_tool:   completed  goal_uuid=c04495ba...
```

**抓到的三个 bug**:

1. **`manipulation.pick_and_place` 根本不在能力注册表里。** `/skills/pick_and_place`
   有真的 action server,但 `skills/manipulation/schema.json` 没声明它,L1 词表里也
   没有这个名字。结果是**我昨天写的 L2 适配器的抓取映射对着真栈必然被拒**。
   修法不是把它加进模型可见列表——那会让模型绕过状态机自己抓——而是新增
   `model_visible` 字段:运行时可派发、模型不可见。合并两个来源时"任一来源不可见则
   不可见"。

2. **能力的 `timeout_s` 从来没被用过。** `ToolClient` 算出 `policy.timeout_s` 就扔了。
   于是所有命令都跑默认超时,schema 里的 `timeout_sec` 只是注释。校准的
   `nav_timeout` 探针正需要"先短超时再正常重试",拿不到就测不了。修法:
   `_effective_timeout` 取 `min(请求值, 能力上限)`——调用方可以要得更短,不能要得
   更长。

3. **探针硬编码了 `"wrench"`,而栈的桩检测器发布 `hex_key_3mm`。** 结果是
   `grasp_failure 0/3`——探针在测一个不存在的工具,然后把"机器人根本没遇到这个
   故障"报成"恢复率 0%"。这比不报更糟。修法:工具名从 runner 传入
   (`--tool-id`),并且探针必须**先确认故障真的产生了**,否则抛
   `ProbeUnavailable`——干净路径的结果不能算作恢复。

**第一份真实测量**(3 样本,不是 30,所以全部 `insufficient_samples`):
```
nav_timeout         1.000 (3/3)
low_confidence      1.000 (3/3)
tool_missing        1.000 (3/3)
grasp_failure       0.000 (3/3)   ← 修完第 3 条后需要重测
identity_mismatch   0.000 (3/3)   ← 同上
```
四个场景族如实拒测并给出场景要求。

**另外确认了一件事:`tc netem` 不需要 sudo。** `unshare -Ur -n` 能拿到自己命名空间
内的 CAP_NET_ADMIN,`tc qdisc add` 成功;命名空间外是 `Operation not permitted`。
代价是节点、工具客户端和注入器必须**全在同一个 namespace 里**。已写进
`docs/specs/wire-faults.md` §7。

**验收命令**:
```bash
bash scripts/run_gazebo_demo.sh --workshop      # 起栈
SMOKE_USE_RUNNING=1 bash scripts/smoke_ros2_runtime.sh
python -m agent_env.scripts.run_calibration --samples 3 --tool-id hex_key_3mm
```

**结果**:428 passed / 3 skipped(主)+ 59 passed(harness)。

**结论**:对着真栈跑一小时抓到的缺陷,比纯逻辑测试一周抓到的多。特别是第 3 条——
**探针测了自己造不出来的故障,然后把结果报成 0%**——这是"测量"最坏的失败方式:
它看起来像数据。

**遗留**:`grasp_failure` 和 `identity_mismatch` 修完后需要重测;`MISSION` 的
`arm_skill_server` 是仿真执行器(不接 MoveIt2),所以抓取类探针测的是仿真行为;
栈跑完已按要求关掉。

---

## 2026-11-10 | **O3.1** 并发的 rclpy 半边跑起来了（真执行器 + 真 action server）

**内容**:上一条把每资源锁和"不等 spin"写了,但那只是纯 Python 的半边。另一半只有
rclpy 能回答:`ReentrantCallbackGroup` 到底会不会让两个资源真的同时推进。

**新测试**(`agent_harness/tests/test_concurrency_ros.py`):真
`MultiThreadedExecutor`、真 callback group、真 action server(故意睡 0.4 秒,
并记录每个 goal 的起止时间),查两件事:

- **不同资源在时间上重叠**(executor 层直接派发)
- **同一资源互斥**(走 `RosBackend`,因为锁在那里)
- **走 `RosBackend` 时不同资源仍然重叠**(证明锁是按资源的,不是一把全局锁)

**踩到的坑**:`RosToolExecutor` 调 `rclpy.init(args=None)`,在 pytest 下会把
pytest 自己的命令行交给 rclpy 的参数解析器。修法是在 fixture 里临时把
`sys.argv` 缩成一个元素——不改生产代码。另外 `rclpy.init` 只能调一次,所以执行器
的初始化必须在测试服务器建好之前发生。

**接入 CI**:无 rclpy 的环境里整个模块跳过(`pytest.importorskip`),所以这个测试
只在 CI 的 ROS 容器 job 里跑——那正是它该待的地方。

**验收命令**:
```bash
source /opt/ros/humble/setup.bash && source install/setup.bash
python -m pytest agent_harness/tests/test_concurrency_ros.py -q
```

**结果**:
- 带 ROS:62 passed;不带 ROS(CI 的 harness job):59 passed, 1 skipped。
- 三个测试都真跑了(不是跳过)。

**结论**:O3.1 的"rclpy 层"从"没验证过"变成"CI 里每次都验"。

**遗留**:**全栈仍未跑(NOT RUN)**。测试证明的是执行器和后端面对真 action server 时
行为正确;它没有证明真的导航服务器、真的机械臂服务器、任务状态机和 Nav2 在底盘
和机械臂一起动时也正确。那需要把 Gazebo 栈起起来,旁边站个人。

---

## 2026-11-10 | **O3.1** 线缆故障注入：判据先写，工具照着实现

**内容**:上一条把 netem 注入和三条不变量做出来了,但"什么算通过"其实还是我说了算。
这一条把**判据先写下来**,工具再照着实现——顺序反过来,是为了让工具能被文件检查,
而不是让文件去追工具。

**判据**(`docs/specs/wire-faults.md`):十一条,分三类。

*安全类* WF-S1..S6:
| ID | 断言 | 拦的是什么 |
|---|---|---|
| S1 | 没有命令被派发超过一次 | 结果丢了之后被重试,机器人动两次 |
| S2 | 结果不确定的命令记为 `unknown`,绝不记 `failed` | 记 `failed` 等于断言"没发生",而这正是招来重试的话 |
| S3 | 有未决命令且未确认时,不派发任何副作用 | 失败关闭 |
| S4 | 每条提交过的命令在账本里都有终态或 unknown 的行 | 命令从账本里消失,和"从没跑过"无法区分 |
| S5 | 没有回执声称 completed 而 call_ok 为假 | 丢包下"结果到了"和"机器人做了"是两件事 |
| S6 | harness 自己没有重复提交同一个命令 id | harness 拿重试掩盖超时,那是 bug 不是修复 |

*活性类* WF-L1、L2:丢包 ≤20% 时至少有一条命令走到非 unknown 的终态;故障撤掉后
新命令必须完成。

*设置类* WF-C1..C3:故障确实下过、确实撤干净、之前有干净基线。

**两个设计要点**:
1. **安全和活性一起判**。一个拒绝所有命令的系统满足每一条安全判据,却毫无用处。
   所以活性判据写明"在链路应该还能用的丢包率下,必须仍然有进展"——60% 丢包时拒绝
   是对的,15% 丢包下什么都不完成是 bug。
2. **判定三态,不是两态**。设置类判据失败 = 本次运行 **void**(无效),不是 fail。
   把无效运行报成系统失败是另一种撒谎,而且是最常见的一种:**配错的注入,和一个
   根本没遇到故障却"扛住"了故障的系统,长得一模一样**。

**工具**按固定顺序跑:干净基线 → 加故障并探测 → 故障下派发 → 撤故障并探测 →
故障撤掉后的恢复派发 → 审计账本。11 条判据全部从账本判定。

**验收命令**:
```bash
python3 scripts/wire_fault_check.py bursty_loss --dry-run
python -m pytest agent_harness/tests/test_wire_faults.py
```

**结果**:
- 30 个测试(每条判据一个"成立"和一个"不成立"——不能失败的检查不算检查),
  harness 套件 59 passed。
- 412 passed / 3 skipped(主套件)。

**结论**:线缆层的"通过"现在是**一份文件里的十一条断言**,不是一次实验的印象。

**遗留**:**未运行(NOT RUN)**——需要 root、活栈和人在场(命令会动机器人)。文档里写明
了不覆盖什么:没有硬件、默认不按端口过滤(CycloneDDS 端口动态,漏掉流量的过滤器会
产出"证明不了任何事"的绿色结果)、审计是事后的、以及单机单时钟。

---

## 2026-11-10 | **O3.1** 线缆故障注入：netem + 三条不变量 + ledger 审计

**内容**:进程内注入的八种传输故障证明的是**状态机**,不是传输层。真实链路可以在
机器人已经动了、但还没人知道的时候丢消息。补上这一半。

**工具**(`scripts/wire_fault_check.py`):对网卡施加 `tc netem`,派发一条命令,
**在 `finally` 里恢复网卡**,然后审计账本。三条规则:
1. **网卡一定在 `finally` 里恢复**。崩了却留下 netem 规则,之后所有实验都是错的,
   而且没人会归因到这个工具。
2. **故障就位之前不派发任何命令**。把干净路径的结果当成故障路径报告,比不跑更糟。
3. **判定来自账本,不来自脚本**。"同一条命令是否被派发两次"是关于账本的事实。

**三条不变量**(`agent_harness/runtime/wire_faults.py`),每条拦一种具体故障:

| 不变量 | 拦的是什么 |
|---|---|
| `no_duplicate_effect` | 超时的命令被派发两次——机器人动两次 |
| `uncertain_stays_unknown` | 超时被记成 `failed`,而这正是招来重试的那个断言 |
| `blocked_until_ack` | 有未决命令时仍派发副作用 |

五套场景:`dropped_result`、`slow_ack`、`duplicated_packets`、`bursty_loss`、`severe`。

**验收命令**:
```bash
python3 scripts/wire_fault_check.py dropped_result --dry-run
python -m pytest agent_harness/tests/test_wire_faults.py
```

**结果**:
- 19 个新测试(规则构造、先清后加、端口范围被拒绝而不是近似、三条不变量各自的
  正例反例、ack 之后解禁、命令不会挡住自己)。
- 405 passed / 3 skipped(主)+ 48 passed(harness)。

**结论**:线缆层的判据现在是**可执行的断言**,不是文档里的期望。

**遗留**:**未运行(NOT RUN)**——需要 root、活栈和人在场(命令会动机器人)。另有两处
如实写下的局限:(1) 默认**不按端口过滤**,因为 CycloneDDS 端口是动态的,过滤器可能
静默漏掉流量——而漏掉流量的过滤器会产出一个"证明不了任何事"的绿色结果;默认覆盖
整个 loopback,在专用测试机上可以接受,共享机器上不行。(2) 审计是**事后**的,读最终
状态再走事件序,不实时监控。

---

## 2026-11-10 | **O6** 校准场景道具 + 不动机器人的布景驱动

**内容**:上一条把校准执行器做出来了,但四个场景族只会打印"需要摆场景"——"摆场景"
一直是一句话。补上道具和布景驱动。

**道具**(`src/robot_collab_sim/models/calibration/*.sdf`):遮挡板、站位阻挡块、
落地工具,三个静态模型。

**驱动**(`scripts/calibration_scene.py`):通过 Gazebo 服务 spawn / delete,
`arm` / `clear` / `show` / `list` 四个动作。

**三个设计决定**:
1. **按站点偏移放置,不写死世界坐标**。站点表改了,场景跟着走,不会悄悄失效。
2. **布景不派发任何机器人技能**。这个脚本只 spawn/delete 静态道具,所以布置场景
   **不会动机器人**;跑探针会。把这两件事分开,是因为前者可以安全地脚本化,后者
   必须有人点头。
3. **`user_cancel` 没有道具**。取消是事件不是布置,脚本打印人工步骤,不假装能摆出来。

**验收命令**:
```bash
python3 scripts/calibration_scene.py list
python3 scripts/calibration_scene.py show visual_occlusion
python -m pytest tests/test_calibration_scene.py
```

**结果**:
- 9 个新测试:SDF 合法且 `static`(不 static 的会倒,倒了的道具就不是那个场景了)、
  文件名与模型名一致、道具引用的站点必须在站点表里、**遮挡板真的在起点和货架之间**
  (否则探针测的是空气)、无道具的场景必须写明人工步骤、`list`/`show` 在无仿真时可用。
- 405 passed / 3 skipped。

**结论**:四个场景族的"要摆什么"现在是一个能执行的脚本,不再是一句描述。

**遗留**:**未在 Gazebo 里试过(NOT RUN)**——没有 spawn 过任何道具,也没有跑过任何
探针。脚本依赖 `gazebo_msgs`(随 `ros-humble-gazebo-ros-pkgs` 一起装),在没装
Gazebo 的环境里会在 import 处失败,这是预期行为而不是需要补的依赖声明:`scripts/`
不在任何 colcon 包里。

---

## 2026-11-10 | **O6** 校准执行器：五个族能测，四个族诚实拒测

**内容**:`l2_calibration.py` 写明了每族"要观察到什么才算 L1 说得对",但一直没有
去观察的那一半。补上:`agent_env/calibration_runner.py` + `scripts/run_calibration.py`。

**两个设计事实**:
1. **有些族不是靠调用能力造出来的**。遮挡、掉落、阻塞交接、运动中取消都需要先把
   场景摆好;任何能力调用序列都造不出来。这些探针抛 `SceneRequired` 并说明要摆什么,
   而不是悄悄测了别的东西。
2. **问"这族能不能测"不能动机器人**。`skipped()` 检查探针本身的标注
   (`scene_required`)而不是运行它们——为了回答一个问题就去派发真实命令,等于让
   提问产生了物理副作用。

**能测的五族**:`nav_timeout`(同一站点先短超时再正常重试)、`grasp_failure`(连续
两次 pick)、`low_confidence`(两次 query_state)、`identity_mismatch`(两次 verify)、
`tool_missing`(query_state 有应答)。**拒测的四族**:`visual_occlusion`、`tool_drop`、
`no_progress`、`user_cancel`,各自打印需要摆的场景。

**验收命令**:
```bash
python -m agent_env.scripts.run_calibration --plan
python -m pytest tests/test_calibration_runner.py
```

**结果**:
- 12 个新测试:探针行为、场景族拒测不产生样本、`skipped()` 不派发任何命令、样本数
  就是尝试数、<30 样本进不了 calibrated、整条链 runner → log → manifest。
- 396 passed / 3 skipped。

**结论**:校准这条链现在从"要观察什么"一路通到"manifest 怎么写",中间没有一段是
空的。缺的只有那四个 Gazebo 场景和一个按按钮的人。

**遗留**:**ROS 路径未跑(NOT RUN)**——本机没起栈。真跑起来会动机器人,所以必须由
人在场。`user_cancel` 的 scene_setup 补上了(之前是空的,导致拒测理由为空)。

---

## 2026-11-10 | **O3.1** ROS 侧并发：每资源一把锁 + MultiThreadedExecutor

**内容**:调度器一直按资源分组、不同资源并行,假后端也证明了;但 ROS 侧没跟上
——一个节点单线程 spin、`RosBackend` 一把全局锁,于是不管调度器允许什么,每次派发
都串行。

**改动**:
- `agent_harness/runtime/concurrency.py`:`ResourceLocks`(每个 resource key 一把锁,
  首次使用时创建)和 `wait_for_future`(等完成信号,不 pump `spin_once`)。
- `RosBackend` 由全局锁改成按 `request.resource_key` 取锁:机械臂命令不再等底盘命令,
  但同一个机械臂上的两条命令仍然互斥——它们抢的是同一台硬件。
- `RosToolExecutor(concurrent=True)`:后台线程跑 `MultiThreadedExecutor`,每个资源
  一个 `ReentrantCallbackGroup`;`_spin_until_done` 在这种模式下改成等信号,不再
  从等待线程 spin——否则一个节点会有两个 spinner,回调顺序就不可预测了。
- CLI 加 `--concurrent`;默认仍是原来的串行路径,什么都没变。

**验收命令**:
```bash
python -m pytest agent_harness/tests        # 29 passed, 含 10 个新并发测试
python -m pytest
```

**结果**:
- 10 个新测试:两资源可重叠、同资源互斥、锁按需创建、已完成 future 立即返回、
  等待中途完成、超时、并发模式不 spin、串行模式保持原行为、后端两资源峰值=2、
  同资源峰值=1。
- 384 passed / 3 skipped(主套件)+ 29 passed(harness 套件)。

**结论**:调度器承诺的并发现在在 ROS 侧也成立了——至少逻辑上。默认路径没有改,
所以这次改动不会在没有验证的情况下改变任何现有行为。

**遗留**:**rclpy 的真实路径没跑过(NOT RUN)**。要证明 `ReentrantCallbackGroup`
确实让底盘和机械臂同时推进、以及 `wait_for_future` 不会漏掉执行器线程解析的完成
信号,需要真节点和真 action server,本机没有。线缆层故障注入(DDS 丢包)仍未做。

---

## 2026-11-10 | **O6 / O7** L2 适配器 + Phase 7 验收补齐 + 编号理清

**内容**:三件事。(1) 把两套 Phase 编号理清——仓库里有三条线各自用过 "Phase N";
(2) 把 OpenRUA 线真正剩下的活做完:L2 适配器、实时栈评测、Phase 7 那四条一直
没实现的验收;(3) 剩下的 GPU 相关实验保持 pending,不假装。

**编号理清**:
- 三条线现在各有前缀:**R**(机器人本体,`docs/roadmap.md`)、**PT**(后训练,
  `EXPERIMENT_LOG.md`)、**O**(OpenRUA 吸收,`docs/specs/phase0-7/`)。
- `docs/specs/README.md` 是地图:每阶段产出了什么、还剩什么。
- `docs/roadmap.md` 重写为三条线的总览。
- 每个 spec/task 文件带横幅标明所属线;状态行改成真实的 "complete"。
- 任务清单按事实勾选:Phase 5、Phase 7 当时全部未勾但代码已合入;Phase 3 的两项
  遗留、Phase 4 的快照接线、Phase 6 的校准如实标注。

**L2 适配器**(`agent_env/l2_gazebo.py`):把实时 ROS 2/Gazebo 栈装进 L1 同样的
`reset()/step()` 形状。写它的时候有三件事和之前的假设不一样,都写进了
`docs/openrua_audit/gazebo-l2.md`:
1. **动作词表不同构**。L1 十三个细粒度动作,live stack 六个可调用能力(两个是复合
   的),检测是"发布"而不是"请求"。映射表 `ACTION_MAP` 全部列出;没有对应的动作
   **带原因拒绝**,不是静默跳过——live stack 上"不支持"和"什么都没做"是两种不同
   的故障,后者正是机器人动两次的路径。
2. **live stack 报不出 L1 报得出的东西**。`holding_tool` / `operator_verified` 在
   mission state machine 内部,没有发布,观测里就写"未知"而不是猜:未确认的物理
   效果不是成功,未知也不是策略能据以行动的 `False`。
3. **episode 的单位不同**。live stack 只有一个复合能力 `mission-control.deliver_tool`,
   所以 L2 的一个 episode 是一次*任务*,L1 的一个 episode 是一个*回合*。同一套
   评测代码、同一个报告生成器,但 L2 报告里带 `unit: mission`,不会被读成回合。

**实时栈评测**(`training/evaluate_l2.py`):用 L1 的 `evaluate_records`、同一个报告
生成器、同一套运行记录给 live stack 打分,并计算回执级指标(不确定结果率、延迟
分位)——只有真实任务会产生回执,轨迹不会。

**Phase 7 验收补齐**(`tests/test_phase7_closure.py`):O7 写了四条一直没实现的:
- **P7-R1** `MultiTurnDeliveryEnv` 可以带 provenance,写出的记录是 v2(带
  `schema_version`、provenance 块和从环境结果推出的回执);不带 provenance 的记录
  一模一样还是 v1——没有多余键,老语料的字节不变。
- **P7-R2** 数据集构建器只在记录带 provenance 时才把它写进 sample meta,所以 v1
  语料产出完全相同。
- **P7-R3** loader 拒绝 prompt/schema 与运行记录不一致的样本;`train_sft` /
  `train_dpo` 加了 `--run-record` 强制它;v1 样本照旧接受。
- **P7-R8** DPO 构建器给克隆做状态哈希校验,对不上的克隆拒绝而不是照样打分。
- **P7-R9** 报告生成器拒绝声明物理结论字段的实验矩阵。

**验收命令**:
```bash
python -m pytest tests/test_l2_adapter.py tests/test_phase7_closure.py
python -m training.evaluate_l2 --dry-run
python -m pytest
```

**结果**:
- 25 个新测试(映射表、拒绝、结果映射、观测不猜、四条 Phase 7 验收)。
- 384 passed / 3 skipped。

**结论**:OpenRUA 线现在只剩三件:ROS 侧的并发(Per-resource callback group)、
线缆层的故障注入(DDS 丢包)、以及校准的**执行器**和它需要的 Gazebo 场景。前两件
不需要 GPU;第三件需要把仿真栈跑起来,但现在有适配器和评测器可以承接。

**遗留**:L2 适配器与实时评测的 ROS 路径**没有在真栈上跑过**(NOT RUN)——本机
此刻没有起 Gazebo。纯逻辑部分(映射、拒绝、结果映射、观测)有测试覆盖。

---

## 2026-11-10 | **O6** L1↔L2 校准契约 + 报告头声明"仅 L1"

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

## 2026-11-10 | **PT** 显存配置修复:rollout 预算预检 + teacher 独立服务

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

## 2026-11-10 | **PT / O7** 实验基线复现 + results.md 生成机制收尾 + 显存预算

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

## 2026-08-25 | **PT4** 后训练栈落地(training/)+ 冻结 benchmark + 两处环境奖励缺陷修复

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

## 2026-07-17 | **PT0** agent_env 环境层建成 + L1 验收 PASS

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

## 2026-07-17 | **PT2** 仓库可用性验证 + 关键 bug 修复(WSL Ubuntu-22.04-External, ROS2 Humble)

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
