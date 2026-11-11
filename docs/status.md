# Where this project is

Three lines of work, one status page. The per-phase detail is in
[`specs/README.md`](specs/README.md); this is the answer to "what is actually
true right now, and what is in the way".

Updated 2026-11-11.

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
| Contracts, runtime, ledger, snapshots, agent loop | complete | 59 harness tests |
| Per-resource concurrency | **verified with ROS 2** | `test_concurrency_ros.py` in the CI ROS job |
| L2 adapter over the live stack | **verified** | runtime smoke test: 2 commands, goal uuids correlated |
| The seven named baselines | **built** | `agent_harness/agent/conditions.py`, `run_conditions.py` |
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
exists to change that. Two families have been measured at 30 samples each, and
**both disagree with L1**:

| Family | L1 says | Live stack (n=30) | Wilson 95% | Verdict |
|---|---|---|---|---|
| `identity_mismatch` | 0.346 | 0.000 | `[0.000, 0.114]` | `interval_excludes_l1_rate` — L1 says a face mismatch heals a third of the time; the stack says never |
| `nav_timeout` | 0.665 | 1.000 | `[0.886, 1.000]` | `interval_excludes_l1_rate` — L1 says a third of its episodes need two timeouts; the stack recovered first time, 30/30 |
| `grasp_failure` | 0.659 | **not producible** | — | the simulated arm never fails a grasp, so the probe refuses with that reason |
| `low_confidence`, `tool_missing` | 0.500 / 1.000 | **scene not arranged** | — | a marginal detection and an absent tool have to be arranged, not called for |
| `visual_occlusion`, `tool_drop`, `no_progress`, `user_cancel` | 1.000 | **scene not arranged** | — | props exist; nobody has placed them |

The two measured rows are the result that matters: **L1 errs in both
directions.** It over-estimates self-healing for identity mismatches and
under-estimates reliability for navigation retries. A policy trained on L1 would
wait for a retry that never comes in one case and give up too early in the other.

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

Blocks: the remaining calibration families, and therefore any statement that the
L1 fault model has been validated. The props exist and the driver exists; nobody
has placed them. `grasp_failure` additionally needs a tool that slips, which the
simulated arm cannot produce at all.

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

Blocks: the transport-safety claim. The criteria, the injector and the audit are
built; the run needs a live stack and moving commands. `tc netem` was verified to
work unprivileged inside `unshare -Ur -n`.

### 6. MoveIt2 or a vendor SDK

Blocks: any physical-manipulation claim. Replacing the simulated executor in
`arm_skill_server` is the whole integration point.

### 7. Hardware

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
