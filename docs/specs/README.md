# Phase numbering in this repository

Three unrelated lines of work have each used "Phase N". They are still called
that in old commits and old logs, so this file is the map: **every phase
reference should carry the prefix of the line it belongs to.**

The line prefixes, and the phase numbering inside each plan, are audited
against the plan text in
[`completion-audit.md`](completion-audit.md) — read that for what is finished
and what is not.

| Prefix | Line | Where its phases are recorded | What it is |
|---|---|---|---|
| **R** | Robot body | [`docs/roadmap.md`](../roadmap.md) | The ROS 2 robot itself: sensors, arms, navigation, simulation, hardware. |
| **PT** | Post-training | [`EXPERIMENT_LOG.md`](../../EXPERIMENT_LOG.md) | The learning stack: `agent_env/` (L1 simulator) and `training/` (SFT/DPO/PPO/OPD). |
| **O** | OpenRUA assimilation | this directory, `phase0/` … `phase7/` | Absorbing the engineering discipline of `Open-RUA/uni-robot-agent`: contracts, a reliable tool runtime, execution evidence. |

The confusion was worth a file of its own: the OpenRUA master plan's Phase 7
is called "training integration", which reads like "we started reinforcement
learning" — but the SFT/DPO/PPO/OPD pipelines predate that work by three
months and are **PT**, not **O**. What O7 did was make the *existing* training
produce numbers that can be trusted.

## The OpenRUA line: O0 – O7 (complete)

Master plan: [`docs/plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md).
Audit: [`docs/openrua_audit/`](../openrua_audit/).

| Phase | What it produced | Where |
|---|---|---|
| **O0** | Two-repository audit; 22 upstream mechanisms each judged ADOPT / ADAPT / REFERENCE / REJECT | `docs/openrua_audit/00_baseline` … `05_adoption_decisions` |
| **O1** | Versioned step contract: command identity, execution status with `unknown`, receipts, snapshots, capability registry | `agent_harness/contracts/` |
| **O2** | Local reliable tool runtime: per-resource FIFO, cancel-queued-not-running, ROS 2 behind a client | `agent_harness/runtime/{scheduler,client,fake_backend,ros_backend}.py` |
| **O3** | Durable execution ledger, fail-closed reconciliation, operator control paths | `agent_harness/runtime/{ledger,control}.py` |
| **O4** | Immutable observation snapshots, freshness gates, capability discovery from the registry | `agent_harness/runtime/snapshots.py`, `agent_harness/contracts/capabilities.py` |
| **O5** | The closed loop: observe → decide one action → validate → submit → await receipt | `agent_harness/agent/` |
| **O6** | L1 receipts in the ROS shape, Wilson-interval calibration, receipt metrics, equal-budget ablations | `agent_env/calibration.py`, `training/{receipt_metrics,ablation}.py` |
| **O7** | Training integration: sample provenance, run records, generated results, reward-hacking audit, counterfactual gate | `training/{provenance,run_record,report,audit,counterfactual}.py` |

## The OpenRUA line: what is still open

| Item | Phase it belongs to | Why it is still open |
|---|---|---|
| The Gazebo scenes for the four scene-dependent calibration families | O6 | The props and the driver exist (`scripts/calibration_scene.py`), but no prop has been spawned in Gazebo and no probe has run. |
| Running the wire faults | O3.1 | The injection tool, five scenarios and the three invariants are built and tested; running them needs root, a live stack and a person. |

Done since the line was first planned, and worth knowing about:

| Item | Phase | Where |
|---|---|---|
| MultiThreadedExecutor + per-resource callback groups | O3.1 | `agent_harness/runtime/concurrency.py`, `RosBackend`, `RosToolExecutor(concurrent=True)`, `--concurrent` |
| Wire-fault injection and the three invariants | O3.1 | `agent_harness/runtime/wire_faults.py`, `scripts/wire_fault_check.py` |
| The wire-fault **acceptance criteria**, written before the tool | O3.1 | `docs/specs/wire-faults.md` — eleven criteria in three kinds, with thresholds and the reasons for them |
| The L2 adapter: the live stack behind `reset()`/`step()`, with the action mapping written out and the unsupported actions refused | O6 | `agent_env/l2_gazebo.py` |
| The live stack scored by the L1 evaluator, with the receipt metrics | O6 | `training/evaluate_l2.py` |
| The L1 side of the calibration: per-family reference rates derived from the configuration, and the measurement programmes | O6 | `agent_env/l2_calibration.py`, `agent_env/scripts/calibrate_l2.py` |
| The calibration runner, and the scene props it needs | O6 | `agent_env/calibration_runner.py`, `scripts/run_calibration.py`, `src/robot_collab_sim/models/calibration/` |
| The Phase 7 acceptance criteria the merge left open: provenance on v2 records, drifted corpora refused, the verified clone guard, physical claims refused | O7 | `tests/test_phase7_closure.py` |
| The seven named baselines, as declarative conditions: `rules`, `expert`, `planner_once`, and four loop configurations that switch on receipts, the freshness gate and recovery in turn | O6 | `agent_harness/agent/conditions.py`, `agent_harness/scripts/run_conditions.py` |

Three of those are **code complete but NOT RUN against a live stack**: the
concurrent executor needs a node and an action server, the wire faults need
root and a moving robot, and the L2 adapter needs Gazebo. All three have their
pure logic covered by tests that need no ROS 2.

The concurrency one has since gone further: `test_concurrency_ros.py` runs a
real `MultiThreadedExecutor`, real callback groups and a real action server,
and checks that different resources overlap while the same resource stays
exclusive. It runs in the ROS container job of CI.

The six experiment arms of **PT** are blocked on GPU, not on code. They stay
`pending` in `docs/results.md` until they run.

## Rules

1. A new phase gets a prefix: `R{n}`, `PT{n}` or `O{n}`. Never a bare "Phase N".
2. The directory names under `docs/specs/` stay `phase0/` … `phase7/` because the
   master plan refers to them; the banner inside each file states the prefix.
3. A phase is only complete when its spec, its tasks, its tests and its
   documentation are on `main`. The status line in the spec file says so.
