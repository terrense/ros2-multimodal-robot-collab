# Phase 6 Spec — L1/L2/L3 Evaluation Alignment

> **OpenRUA assimilation — Phase O6.** Not to be confused with the post-training
> line (`PT6` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R6` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

Status: **complete — merged to `main`**
Master plan: [`../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md) §Phase 6
Depends on: contract v1.2, `ToolClient` + ledger (Phase 2–3), `model_view`
(Phase 4), `ClosedLoop` + budgets (Phase 5).

## Goal

Make the numbers mean the same thing at every level. L1 stays the large-scale
rollout engine; L2 (live ROS 2/Gazebo) is reachable through the **same high-level
action and observation semantics**; and L1's fault behaviour is calibrated
against measured L2 results instead of assumptions. L3 hardware stays out of
scope until separately authorized.

## What already exists

| Piece | Where | Used by Phase 6 for |
|---|---|---|
| 400-episode frozen benchmark + manifest | `benchmarks/fault_injection_v1.jsonl`, `manifest.json` | the held-out evaluation; never modified |
| 9 fault families, unrecoverable set, compatible pairs | `agent_env/faults.py` | L1 fault semantics and calibration targets |
| L1 step environment + episode summary | `agent_env/l1_sim.py` | L1 rollouts |
| Metric set + failure taxonomy + buckets | `training/evaluate.py` | baseline of the metric contract |
| Receipts, ledger, fail-closed gate | `agent_harness/runtime/` | receipt-level metrics and duplicate/uncertain detection |
| `ClosedLoop`, budgets | `agent_harness/agent/` | equal-budget comparison and ablations |

## 1. One semantic layer, three levels

```
                 Action (skill.action, args) + Observation / StepResult
   L1  L1DeliveryEnv (text simulator, fast, 400-episode benchmark)
   L2  L2GazeboAdapter (live ROS 2, receipts from ToolClient)
   L3  hardware (out of scope; separately authorized)
```

The adapter contract is the existing L1 one (`agent_env/protocol.py`):
`reset() -> Observation`, `step(Action) -> (Observation, reward_components, done, info)`.

**L2 adapter rules:**

- one `Action` becomes one `ActionRequest` through `ToolClient`; the resulting
  `ExecutionReceipt` becomes the `StepResult` (`call_ok` → status,
  `failure_code` → `error_code`, `physical_effect_verified` → `data`);
- an `unknown` receipt maps to `StepResult(status="unknown")` — it is neither
  success nor failure, and the episode continues only if the policy chooses a
  recovery that does not resubmit the same `command_id`;
- observations come from the Phase 4 snapshot publisher (freshness gate
  applied), never from raw ROS internals.

**L1 backend** (`agent_harness/runtime/l1_backend.py`) lets the Phase 5 loop run
L1 episodes and emit receipts, so both levels produce the *same* receipt shape.
Without it, L1 metrics and L2 metrics are computed from different structures.

## 2. Calibration, measured not assumed

Current L1 fault probabilities are configured, and the policy never sees the
fault type (`agent_env/faults.py:1-6`). That is correct for ground-truth
isolation but leaves one open question: **do L1's fault frequencies match what
the live stack actually does?**

`agent_env/scripts/calibrate_l2.py` (new) must:

1. run a scripted L2 programme per fault family on the Gazebo stack (N ≥ 30 per
   family, seed recorded);
2. measure the observed transition frequency of each fault's effect
   (navigation timeout rate, grasp failure rate, drop rate, detection
   confidence distribution, cancel timing);
3. compare against the L1 configuration and write
   `benchmarks/calibration_v1.json` with per-family: L1 value, L2 estimate,
   Wilson 95% interval, sample count;
4. mark a family `calibrated: false` when the interval does not contain the L1
   value, and never write "calibrated" from fewer than 30 samples.

No L1 probability may be described as "measured" in `docs/results.md` until its
manifest entry says `calibrated: true`.

## 3. Metrics, extended to receipts

`training/evaluate.py` already reports success, recovery, invalid skill rate,
safety violations, actions, repeats and stalls, plus terminal classes and
behavioural flags. Phase 6 adds a receipt layer computed from ledger/receipts:

| Metric | Definition |
|---|---|
| `uncertain_outcome_rate` | receipts with `execution_status=unknown` / calls |
| `duplicate_effect_rate` | commands whose ledger row shows more than one dispatch / calls |
| `false_success_rate` | episodes where the policy claimed success without environment confirmation |
| `premature_stop_rate` | episodes ending with a non-terminal last receipt |
| `p50/p95_latency_ms` | receipt `finished_at - started_at`, per capability |
| `actions_cost`, `token_cost` | Phase 5 budget usage per episode |

Every run records the benchmark manifest hash, seed, model/prompt versions and
budget settings, so two runs are comparable only if those match.

## 4. Equal-budget comparison protocol

- All baselines run with the same `Budgets` (turns, characters, wall).
- The ablation table reports the budget as a column; a run with a larger budget
  is not compared against a smaller one.
- L1 and L2 runs are compared at equal *action* budgets, not equal wall time
  (L2 is far slower; wall time would silently favour L1).

## 5. Baselines (named conditions)

| Condition | Policy | What it isolates |
|---|---|---|
| `rules` | existing rule baseline | no learning |
| `expert` | scripted expert | upper bound |
| `planner_once` | one-shot mission plan | open loop, no feedback |
| `loop_no_receipts` | closed loop, receipts ignored | value of observing the result |
| `loop_receipts` | closed loop using receipts | receipt value |
| `loop_receipts_fresh` | + snapshot freshness gate | freshness value |
| `loop_receipts_recovery` | + explicit recovery decisions | recovery value |

Each condition is a config, not a fork of the loop.

## 6. Artifacts

- `runs/eval/<condition>.json` — the existing evaluation record plus the
  receipt block;
- `runs/eval/ablation.md` — generated table across conditions with budgets;
- `runs/eval/failures_<condition>.md` — per-failure report (episode id, fault
  family, first divergence, receipts);
- `benchmarks/calibration_v1.json` — as above.

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| P6-R1 | L2 adapter implements the existing `reset/step` contract | adapter tests with a fake backend |
| P6-R2 | L1 backend produces the same receipt shape as L2 | parity test |
| P6-R3 | `unknown` never becomes success or failure in either adapter | test |
| P6-R4 | Calibration writes intervals and refuses to mark <30 samples calibrated | unit test on synthetic rates |
| P6-R5 | Receipt metrics computed from ledger/receipts | unit tests |
| P6-R6 | Ground truth never reaches a model view | leak test over all fault families |
| P6-R7 | Runs record manifest hash, seed and budgets | run-record test |
| P6-R8 | Ablation table refuses runs with mismatched budgets | test |
| P6-R9 | Frozen benchmark unchanged | file hash check |
| P6-R10 | 281 existing tests stay green | test run |

## Non-goals

- No hardware runs, no training runs in this phase.
- No change to the L1 action vocabulary, rewards or the frozen benchmark.
- No new benchmark: calibration annotates the existing one, it does not replace it.

## Open questions for approval

1. **Where the L2 adapter lives.** Recommendation: `agent_env/l2_gazebo.py`
   with lazy ROS imports, so training-side imports stay rclpy-free.
2. **Calibration sample size.** Recommendation: N ≥ 30 per family, Wilson 95%,
   seed recorded; anything less is `calibrated: false`.
3. **Receipt metrics source.** Recommendation: duplicate/uncertain from the
   ledger (durable), latency and costs from receipts.
4. **Ablation runner.** Recommendation: `training/ablation.py` reusing
   `evaluate.py`, not a second metrics implementation.
