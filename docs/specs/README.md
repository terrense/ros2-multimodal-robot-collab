# Phase numbering in this repository

Three unrelated lines of work have each used "Phase N". They are still called
that in old commits and old logs, so this file is the map: **every phase
reference should carry the prefix of the line it belongs to.**

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
| MultiThreadedExecutor + per-resource callback groups | O3.1 | The ROS 2 side serialises every dispatch through one node, so the scheduler's cross-resource concurrency is proved by the fake backend only. |
| Fault injection on the wire (DDS-level drops) | O3.1 | The eight transport faults are injected in-process; proving the real DDS stack survives them needs a live stack under packet loss. |
| `agent_env/l2_gazebo.py` — the L2 adapter | O6 | The live stack is driven command by command; there is no `reset()`/`step()` environment over it, so the same evaluation code cannot score L2. |
| The receipt metrics wired into the evaluation | O6 | `training/receipt_metrics.py` computes them, but the evaluator scores L1 episodes, which carry no receipts. |
| The L2 calibration runner and its scenes | O6 | The contract exists (`agent_env/l2_calibration.py`); what is missing is the runner that executes the per-family programmes on Gazebo and the scenes that make occlusion, a dropped tool and a blocked handover reproducible. |

The six experiment arms of **PT** are blocked on GPU, not on code. They stay
`pending` in `docs/results.md` until they run.

## Rules

1. A new phase gets a prefix: `R{n}`, `PT{n}` or `O{n}`. Never a bare "Phase N".
2. The directory names under `docs/specs/` stay `phase0/` … `phase7/` because the
   master plan refers to them; the banner inside each file states the prefix.
3. A phase is only complete when its spec, its tasks, its tests and its
   documentation are on `main`. The status line in the spec file says so.
