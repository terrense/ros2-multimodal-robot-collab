# Where this project is

Three lines of work, one status page. The per-phase detail is in
[`specs/README.md`](specs/README.md); this is the answer to "what is actually
true right now, and what is in the way".

Updated 2026-11-12.

---

## One paragraph

The robot body runs: ten ROS 2 packages build in CI, the workshop scene comes up
with official KUKA / FANUC / Dobot arms and both sensors, and Nav2 drives the
base to a station. The learning stack runs on CPU: the environment, the data
pipeline, the reward, the credit assignment and the evaluator are all measured,
and the rule baseline reproduces 76.1% bit for bit. The runtime that makes skill
execution trustworthy is complete and verified against a real ROS 2 graph,
including the concurrency work and a full calibration pass. What is **not** done
is the thing the whole research question rests on: **not one model has been
trained**, because that needs a GPU.

---

## R — Robot body

| Piece | State | Evidence |
|---|---|---|
| 10 ROS 2 packages | build | `colcon build` in the CI ROS container |
| Workshop scene: KUKA KR10, FANUC M-10iA, Dobot CR10 on a weighted chassis | works | Gazebo, 2026-11-09 and again 2026-11-11 |
| Sensors: RealSense D435 (RGB-D), Velodyne VLP-16 | publish | topics seen on the live graph |
| Nav2 station navigation | works | `FULL_NAV=1`, goal reached |
| SLAM | **odometry only** | scan matching off: best measured tracking 84% and navigation still failed |
| Arm execution | **simulated** | `arm_skill_server` sleeps `simulate_step_seconds`; no MoveIt2 |
| Face auth / gesture / voice | stubs or vendored source | no camera-driven recognition in the loop |
| Headless stack + one dispatch | **in CI** | `ci.yml` simulation smoke step: gzserver, no window, real delivery |
| Real hardware | **not started** | needs separate authorization, and calibration first |

The gap that matters: **the arm does not really grasp.** Every grasp in this
repository is the simulated executor reporting success. That is fine for
training an agent's *decision* layer, which is what PT optimises, but it means
no claim about physical manipulation can be made from anything here.

## PT — Post-training

| Piece | State | Evidence |
|---|---|---|
| L1 environment: 13 actions, 9 fault families, double faults | works | PT0 acceptance, 140 episodes, all buckets 100% |
| Data pipeline | **verified end-to-end** | SFT corpus, 102 DPO pairs, leak check clean (0 shared scenarios) |
| Reward presets + GAE credit assignment | unit-tested | `tests/test_credits.py`, `test_rewards.py` |
| Four training entry points | written, **never executed on GPU** | CPU-only loss tests |
| Reference measurements | **measured** | rule baseline 76.1%, expert oracle 100% |
| Six experiment arms | **not run** | `docs/results.md`, all `pending` |

**This is the bottleneck.** Everything above is infrastructure for a comparison
that has not happened. The comparison needs roughly: 4×96 GB for the student
plus 2 cards for the 30B teacher, or 4×48 GB for SFT only. The budget and the
two configuration problems that would have caused an OOM are in
[`post_training.md`](post_training.md) §15.

## O — OpenRUA assimilation

| Piece | State | Evidence |
|---|---|---|
| O0–O7 | complete | `specs/README.md` |
| Contracts, runtime, ledger, snapshots, agent loop | complete | 67 harness tests |
| Per-resource concurrency | **verified with ROS 2** | `test_concurrency_ros.py` in the CI ROS job |
| L2 adapter over the live stack | **verified** | runtime smoke test: 2 commands, goal uuids correlated |
| The seven named baselines | **built** | `agent_harness/agent/conditions.py`, `run_conditions.py` |
| Receipt metrics on both paths | L1 **measured**, L2 **not run** | L1: 4205 calls over the frozen benchmark, published in `docs/results.md` |
| Calibration contract + runner + scenes | complete | `agent_env/l2_calibration.py` |
| Calibration measurements | **2 of 9 measured, both falsify L1** | see below |
| Wire faults | built + criteria written, **never run** | `specs/wire-faults.md` |

### What the named conditions measured

The four loop conditions, 30 L1 episodes, rule baseline, identical scenarios:

| Condition | task success | mean turns | what it isolates |
|---|---|---|---|
| `loop_no_receipts` | **3.3%** | 31.1 | — |
| `loop_receipts` | **83.3%** | 10.7 | the receipt |
| `loop_receipts_fresh` | 83.3% | 10.7 | the freshness gate |
| `loop_receipts_recovery` | 83.3% | 10.7 | explicit recovery |

The receipt layer is worth about **eighty points** of task success to the closed
loop: without it the policy cannot tell what happened and loops until the turn
budget (96.7% of episodes hit it).

The last two rows are identical, and that is the honest result rather than a
bug: L1's snapshots are always fresh, and the rule policy does not read the
capability list. **The freshness and recovery conditions need a model policy to
show any value** — with a rule baseline they are no-ops, and reporting that is
more useful than a number that looks like a finding.

Building this also exposed a defect that had been there since Phase 2:
`ToolClient` hard-coded the `ros2` platform, so every L1-only capability was
rejected — and silently, because a rejected receipt is a valid receipt. The
loop ran thirty-two turns of polite rejections while the environment never
advanced a single step.

### Calibration: the state of the fault model

L1's fault behaviour is configured, not measured, and the calibration machinery
exists to change that. Five families have been measured at 30 samples each, and
**four of the five disagree with L1**:

| Family | L1 says | Live stack (n=30) | Wilson 95% | Verdict |
|---|---|---|---|---|
| `nav_timeout` | 0.665 | 1.000 | `[0.887, 1.000]` | `interval_excludes_l1_rate` — L1 says a third of its episodes need two timeouts; the stack recovered first time, 30/30 |
| `identity_mismatch` | 0.346 | 0.000 | `[0.000, 0.114]` | `interval_excludes_l1_rate` — L1 says a face mismatch heals a third of the time; the stack says never |
| `low_confidence` | 0.500 | 0.000 | `[0.000, 0.114]` | `interval_excludes_l1_rate` — L1 gives a re-perceive half a chance; the detector publishes the same confidence every time |
| `user_cancel` | 1.000 | 0.000 | `[0.000, 0.114]` | `interval_excludes_l1_rate` — the motion stops, and the mission reports it as a failed pick rather than as a cancellation |
| `tool_missing` | 1.000 | 1.000 | `[0.887, 1.000]` | `ok` — the mission says "Could not find tool …", and keeps saying so |
| `grasp_failure` | 0.659 | **not producible** | — | the simulated arm never fails a grasp |
| `visual_occlusion` | 1.000 | **not producible** | — | the perception stub publishes a fixed detection and does not observe the world |
| `tool_drop` | 1.000 | **not producible** | — | nothing on the stack releases a held tool |
| `no_progress` | 1.000 | **not producible** | — | nothing consults the handover zone, so it cannot be blocked |

The four rows that disagree point the same way: **L1 assumes a self-healing
the stack does not have.** A face mismatch that heals, a marginal detection
that clears on a second look, a cancel that is recorded as a cancel — the
simulator has all three, the live stack has none. A policy trained on L1 waits
for retries that never come.

Measuring `user_cancel` also found a defect in the runtime itself: an
emergency stop was queued behind the motion it was meant to stop, because a
control command and a motion shared one exclusive resource. A control command
is not a motion — it has its own resource now, and the same probe that
measured 0/5 before the change measures 5/5 after it.

An earlier run reported `tool_missing: CALIBRATED`. That verdict was discarded:
the probe was only asking `query_state` twice, which measures that the stack
answers, not that a missing tool is reported as missing. A liveness probe that
always succeeds matches any L1 rate of 1.0, and the output is indistinguishable
from a validated fault model. Both probes were rewritten to require their scene.

---

## What is actually in the way

Ordered by what unblocks the most.

### 1. GPU time

Blocks: the six experiment arms, and therefore the entire supervision-source
comparison. Nothing else in the repository is waiting on it and nothing else can
substitute for it.

### 2. Real-stack verification is in CI now — and it found a fourth bug

Three defects were found the first time the stack was run for real — a
capability missing from the registry, a timeout that was computed and discarded,
and a probe measuring a tool that did not exist. All three were invisible to
every test in the suite.

The ROS container job now starts `gzserver` with no window, waits for the
mission action server, and drives a real delivery through the runtime. Writing
it found a fourth defect immediately:

> `realsense2_description` and `velodyne_description` are reached by `$(find …)`
> from the robot's URDF and were never declared in any `package.xml`. They
> resolved here only because someone apt-installed them by hand. A clean
> container would have failed at `xacro` — and it would have looked like a
> broken URDF rather than a missing declaration.

That is the "works on my machine" class, and it is now covered statically too:
`tests/test_package_dependencies.py` scans every `$(find X)`,
`FindPackageShare("X")`, `package="X"` and ROS import under `src/` and requires
each referenced package to be declared.

**Still open here:** the CI job proves the graph comes up and a plan dispatches.
It does not drive Nav2, and it does not run a calibration probe. Those stay
manual.

### 3. Four scenes and one unproducible fault

~~Blocks: the remaining calibration families.~~ **The scenes are arranged and
every family has a probe** (2026-11-12). What is left is a limitation of the
stack rather than of the harness, and it is now a stated reason instead of a
silent gap:

- `visual_occlusion` — the perception stub publishes a fixed detection and does
  not observe the world, so an occluder changes nothing. A real detector is
  needed before this family can mean anything.
- `tool_drop` — nothing on the stack releases a held tool.
- `no_progress` — nothing consults the handover zone, so it cannot be blocked.
- `grasp_failure` — the simulated arm never fails a grasp.

Each of those probes tries, and refuses with the mechanism that is missing. The
other five families are measured.

### 4. The interrupted model

~~Blocks a claim that the agent gateway is safe to interrupt.~~ **Done
2026-11-11.** `Interrupted` is a first-class outcome: the decision source can
die (the policy raises) or be interrupted from outside (a watchdog or a
signal handler), and in both cases:

- the episode ends as `interrupted`, with **success unknown rather than false** --
  the episode never finished, and a command in flight may still complete;
- **the physical work is not touched.** An interruption is not a cancellation:
  nobody asked the arm to stop, and a model that died is not in a position to
  authorise a stop;
- the report says how much work may still be running, because "the model died"
  and "the model died and the arm is still moving" are different findings.

Getting the last point right needed the wait for a receipt to become
interruptible: a command that takes thirty seconds used to keep the episode
alive for thirty seconds after the decision source died.

### 5. Wire-fault run

~~Blocks: the transport-safety claim.~~ **Run against the live stack, 2026-11-12.**
The whole experiment runs unprivileged: the stack, the injector and the audit
all live inside one user+network namespace, so the injected loss cannot reach
anything outside it.

Under `severe` (60% loss, 50% correlated, 200 ms delay) the dispatch still came
back `completed` — it took **7.85× as long** (17.48 s against 2.23 s), the
ledger recorded the command once, nothing replayed it, and the command after
the fault was cleared took 2.13 s. All twelve criteria hold.

Two of them were vacuous rather than satisfied: nothing ended `unknown`, so the
uncertainty and fail-closed criteria had nothing to check. The transport
retransmitted what the link dropped. Exercising the fail-closed path needs a
loss beyond what retransmission can cover.

The first run came back **VOID**, and it was right to: the probe asked `tc` for
the root qdisc, and the netem rule is a child of a `prio` band, so the probe
could not see the fault it was meant to confirm. The second run came back
**PASS**, and it was hollow: the fault had not reached the transport at all,
and nothing in the criteria could tell. There is now a criterion for that too
(WF-C4): a fault that costs no time did not happen.

### 6. The headless CI simulation job

~~Blocks nothing, but it was red.~~ **Green since 2026-11-12.** Two failures
had to be fixed before it could pass, and neither was in the code it tests:
the container has no GL driver (mesa plus `LIBGL_ALWAYS_SOFTWARE=1`), and
`rosdep` had to fetch through a mirror that occasionally returns a 502 (three
attempts, and `Acquire::Retries` for every apt-get in the job).

The step now writes its diagnosis into the job summary rather than only into
the log, because the log needs admin rights to download and a failure nobody
can read is a failure nobody can fix.

### 7. MoveIt2 or a vendor SDK

Blocks: any physical-manipulation claim. Replacing the simulated executor in
`arm_skill_server` is the whole integration point.

### 8. Hardware

Blocks: R2 and every sim-to-real statement. Needs separate authorization, TF
calibration and a working stop path first.

---

## What would make this a finished piece of work

The research question is *which source of supervision fixes which failure
mode*. To answer it:

1. **Six arms trained and scored** — the comparison itself.
2. **A trusted fault model** — calibration either passing or, where it fails,
   the L1 number corrected and the arms re-run against the corrected model.
3. **The failure decomposition reported per arm** — which is already what the
   report generates; it needs numbers.

Everything else — the runtime, the ledger, the adapter, the calibration
machinery — exists so that those three statements can be believed.
