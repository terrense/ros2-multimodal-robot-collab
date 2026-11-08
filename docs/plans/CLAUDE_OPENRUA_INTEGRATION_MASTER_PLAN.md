# Claude Code Master Task: Evidence-Driven OpenRUA Assimilation into ros2-multimodal-robot-collab

## Mission
Integrate the *engineering principles and selectively reusable components* of `Open-RUA/uni-robot-agent` into `terrense/ros2-multimodal-robot-collab`, preserving the existing ROS2 execution graph, L1 training environment and SFT/DPO/PPO/OPD pipelines. Do **not** copy the upstream repository wholesale or conflate test simulations with real-robot validation.

Repositories:
- Target: https://github.com/terrense/ros2-multimodal-robot-collab
- Reference: https://github.com/Open-RUA/uni-robot-agent

## Immutable rules
1. Read the actual code, not only README, and cite file path, symbol, line range and pinned SHA for findings.
2. Before edits: record `git status`, current branch, HEAD SHA, worktree changes and dependencies. Do not overwrite user changes. Create an isolated feature branch when authorized and convenient.
3. No upstream copy without provenance and checking MIT license/notice. Existing third_party components have separate AGPL/GPL/noncommercial obligations.
4. No actual hardware motion or untrusted generated code execution without explicit separate permission. For the first several phases use dry-run, fake ROS2, L1 and Gazebo only. Emergency stop is a controller/hardware concern, never an LLM-only mechanism.
5. Never modify model training results to match predicted targets. `docs/results.md` reports 1/7 arms completed; remaining model arms are pending.
6. Each phase has an independent validation gate, tests, documentation and a small commit/PR. Do not perform large rewrites or mass formatting.
7. Maintain L1's public behavior unless a versioned schema migration and tests explicitly justify a change. Preserve the existing 400-episode frozen benchmark and data split isolation.
8. Pause at phase boundaries for review. If unable to run hardware-dependent tests, mark them **NOT RUN**, never **PASS**.

## Baseline (verified directory-level and selected source checks)
Target contains `src/robot_collab_*` ROS2 packages, `agent_harness/scripts/ros_tool_executor.py` (plan-to-ROS2 bridge), `agent_harness/scripts/minimax_mission_planner.py` (single-shot structured planner), `agent_env/protocol.py` and `agent_env/l1_sim.py` (closed-loop step environment), `training/envs/multi_turn_env.py`, `training/rollout.py`, and `training/train_{sft,dpo,ppo,opd}.py`.
Source contains `cli.py`, `config.py`, `session.py`, `agents/`, `provider/`, `context/`, `tools/`, `execution/`, `integrations/`, `services/environment/`, `observation/`, `events/`, `artifacts/`, `telemetry/`, and `resources/skills/`.

**Architectural conflict to resolve:** `agent_harness` currently accepts a multi-step mission plan, whereas `agent_env`/training optimizes one feedback-conditioned action per turn. Converge on a shared step/receipt protocol without eliminating both workflows.

## Phase 0: Reproducible two-repository audit; NO IMPLEMENTATION
- Pin both upstream and target exact HEAD commit SHAs. Inventory tracked files, packages, tests, dependencies, licences, generated assets, and active launch entrypoints. Exclude vendored third-party source from line-by-line business-logic audit.
- Trace target's end-to-end paths: CLI planner -> `SkillRegistry.validate_plan` -> ROS2 executor -> ActionClient -> `mission_state_machine` -> navigation/manipulation actions -> feedback/cancellation; separately training policy -> `MultiTurnDeliveryEnv.step` -> `L1DeliveryEnv.step` -> reward -> record.
- Trace upstream's *actual* order: CLI/config -> session assembly -> agent adapter -> toolkit -> parallel executor -> scheduler -> provider/scene/motion -> environment client -> service/receipt -> backend -> artifact and finalization.
- Create: `docs/openrua_audit/00_baseline.md`, `01_upstream_callgraph.md`, `02_target_callgraph.md`, `03_gap_matrix.md`, `04_licenses.md`, `05_adoption_decisions.md`.
- For every upstream module decide: ADOPT CONCEPT, ADAPT IMPLEMENTATION, REFERENCE ONLY, or REJECT (and why). Distinguish actual verified implementation from hypothesis.
- Validation: no tracked changes outside docs; capture current baseline test/build output.

## Phase 1: Versioned contracts and semantic unification
- Add a typed, ROS2-neutral contract (initially new package/module) for `ActionRequest`, `ExecutionReceipt`, `ObservationSnapshot`, `TaskOutcome`, `FailureCode` and `CapabilityDescriptor`.
- Add `episode_id`, `command_id` (idempotency identity), `snapshot_id`, timestamps, `frame_id`, units, resource key, `state_version`/sequence, execution status (`not_started`, `queued`, `running`, `completed`, `failed`, `unknown`) and explicit task/subgoal outcomes.
- Separate `tool_call_ok` from `physical_effect_verified` and `task_success`.
- Build adapters from existing `agent_env.protocol` and ROS2 Action/Service feedback, without creating competing authoritative state stores.
- Tests: schema round-trips, invalid units/frames, state transitions, backward compatibility and strict rejection of unsupported capabilities.
- Deliverable: `contracts-v1.md` and contract tests.

## Phase 2: Local reliable Tool Runtime (no new LLM features yet)
- Refactor the existing `SkillRegistry` / `RosToolExecutor` behind a model-neutral `ToolClient`, executor and resource-policy interface; keep existing dry-run scripts operational.
- Add per-resource FIFO ordering for mutually exclusive arm/base operations, queued cancellation, bounded waits, timeout propagation and structured results.
- Avoid introducing a second scheduler for ROS2 Actions if the existing Action Server owns sequencing; document the ownership boundary.
- Tests: unknown tool, invalid schema, no resource collision, FIFO execution, cancel queued, interrupt running, timeout, exception path, graceful shutdown with inflight action.
- Deliverable: deterministic fake-backend integration tests.

## Phase 3: Physical execution consistency and safe ROS2 adapter
- Implement an execution ledger storing `command_id -> state/receipt`, with durable recording as needed; reconcile uncertain outcomes before accepting another side-effecting command.
- Build `Ros2EnvironmentAdapter` using ROS2 Action clients, services and state Topics; do not expose ROS2 controller internals to the LLM.
- Define how goal UUID, command ID and receipt are correlated. A network timeout does NOT imply non-execution. Never blindly replay unknown-effect commands.
- Add independently operable stop/pause/cancel paths with bounded acknowledgement and fail-closed policy. Do not call a gesture/topic-level stop a hardware E-stop.
- Tests: dropped ACK after completed motion, stale snapshot, duplicate command, dropped result, server restart, cancellation race, delayed feedback, controller unavailable.
- Deliverable: fault-injection report and verified state machine.

## Phase 4: Observation & prompt/skills layering
- Add immutable observation snapshots with sensor timestamp, camera intrinsics/extrinsics, robot joint state, TF frame and calibration version. Reject incompatible snapshots; implement freshness gates.
- Introduce separated general-purpose skills and environment-specific skills. Existing `skills/` remain the canonical ROS2 execution skills; Markdown guidance is not executable control.
- Model-visible capability discovery must be derived from actual registered operations, not hardcoded prompts. Agent may suggest a recovery; safety/authorization remain deterministic.
- Tests: stale-vision action rejection, frame mismatch, capability mismatch, tool schema consistency, hidden evaluator field exclusion.

## Phase 5: Agent gateway / backend adapters
- Keep one policy interface usable by scripted baseline, model backends and human-debug driver.
- Implement closed-loop `observe -> choose ONE action -> validate -> submit -> await/inspect receipt -> observe` runtime. Preserve existing one-shot mission planner as a supported optional planning macro.
- Only then consider Claude/Codex adapters (via their SDK contracts) and shared prompt/context management. Do not import OpenRUA's entire provider infrastructure unless justified.
- Tests: multiple backends with stub model, retry budget, token budget, premature finish, model interrupted while physical job runs.

## Phase 6: L1/L2/L3 evaluation alignment
- Keep `L1DeliveryEnv` for large-scale rollouts. Add live ROS2/Gazebo `L2` interface under the same *high-level* action/observation semantics; L3 hardware is later and separately authorized.
- Verify that L1 fault transition frequencies and effects are calibrated against L2 measured results instead of invented assumptions.
- Ensure evaluator-only ground truth is never revealed through planner observation/feedback. Freeze seeds/splits; compare equal time, action and token budgets.
- Baselines: existing rules, expert, single-shot LLM planner, closed-loop without receipts, with receipts, with snapshot freshness, with recovery.
- Metrics: task/recovery success, false success, premature stop, invalid tool rate, unsafe requests, uncertain-outcome rate, duplicate effects, p50/p95 execution time, actions and token cost.
- Outputs: versioned benchmark manifests, run artifacts, ablation tables, per-failure reports.

## Phase 7: Training integration AFTER the runtime is credible
- Extend trajectory exporter without changing dataset semantics silently. Tie every sample to model/prompt/schema/benchmark/episode versions and tool receipts.
- Re-run SFT, DPO, PPO and OPD arms as explicitly measured experiments; update `docs/results.md` from scripts only. Compare identical policy inputs and budgets.
- Critically assess credit assignment, reward hacking and simulator-to-Gazebo distribution shift; do not claim physical-robot performance from L1-only data.
- Analyze counterfactual recovery decisions only when matched state restores are actually supported and verified.

## PR acceptance template (mandatory)
Every PR reports: Goal, exact source/target SHA, files/symbols read, design tradeoff, changed contracts, test commands and outputs, benchmark deltas, safety impact, third-party attribution, unresolved risks, rollback path, and next dependency.

## First immediate command to Claude Code
**Execute Phase 0 only.** Do not rewrite code, do not download random dependencies, do not run the robot, do not modify training data, and do not claim completion without source-backed artifacts. When Phase 0 is finished, present the gap matrix and propose the first small Phase 1 PR for review.
