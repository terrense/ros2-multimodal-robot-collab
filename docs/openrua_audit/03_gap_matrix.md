# 03 — Gap Matrix

Compared capability by capability. Status vocabulary:

- **present** — we have an equivalent, at comparable quality
- **partial** — something exists but lacks the mechanism's guarantees
- **missing** — no equivalent
- **duplicated** — two of our own implementations compete
- **incompatible** — our shape cannot carry the mechanism without a contract change

Upstream evidence is relative to the pinned clone (`b972237d`); target evidence
to `e2009d02`.

## A. Contracts and identity

| Capability | Upstream | Ours | Status |
|---|---|---|---|
| Typed action request | `RequestEnvelope` + `RequestIdentity` (`services/environment/contracts.py:211-220`) | `Action` dataclass (`agent_env/protocol.py:77-100`) and a different `SkillCall` (`ros_tool_executor.py:30-35`) | **duplicated** |
| Command identity | `request_id` + monotonic `sequence` + `execution_id` (`contracts.py:211-216`) | `trace_id` per plan only (`ros_tool_executor.py:287`); `episode_id`/`step_index` in training (`multi_turn_env.py:156,193`) | **missing** on path A |
| Execution status taxonomy | `completed/partial/rejected/unknown` (`contracts.py:231`); errors `not_started/partial/unknown` (`:238`) | `ToolCallResult.status` free-form: `pending/validated/succeeded/failed/rejected` (`ros_tool_executor.py:300,318,351,363`); no `unknown` | **incompatible** |
| Observation contract | snapshots with identity, version, freshness (`integration contracts`, `observation/`) | `Observation` (`protocol.py:126-150`) is rich but has no snapshot id, timestamp, frame or calibration version | **partial** |
| Episode outcome | `EpisodeOutcome(success, accepted, retryable, failure_category)` (`integrations/base.py:58-70`) | `Outcome` enum (`protocol.py:66-74`) is richer on failure taxonomy, but has no accepted/retryable distinction | **partial** (ours is better for L1, theirs for lifecycle) |
| Capability discovery | derived from `IntegrationSpec.eval_tool_names` (`session.py:107-115`) | derived from schema files (`ros_tool_executor.py:65-74`, `protocol.py:159-163`) | **present** |

## B. Execution reliability

| Capability | Upstream | Ours | Status |
|---|---|---|---|
| Resource-scoped ordering | `ToolExecutionPolicy(resource_key, concurrency)` + per-resource FIFO (`execution/scheduler.py:21-29, 196-227`) | none; plan executes sequentially (`ros_tool_executor.py:289`) | **missing** |
| Background jobs | job registry, handles, queue position, control tools (`execution/scheduler.py:63-153`; `execution/parallel.py:67-76, 164-169`) | none | **missing** |
| Cancel queued vs running | `cancel_if_queued` (`scheduler.py:131-139`), running work not falsely marked cancelled | none | **missing** |
| Bounded waits / timeout propagation | per-request timeout → `ExecutionUncertain` (`services/environment/client.py:36-39, 246-250`) | one global `--timeout-sec` (`ros_tool_executor.py:415`); schema timeouts unused | **partial** |
| Unknown-outcome handling | client refuses writes while uncertain, `reconcile()` reads receipt without resubmitting (`client.py:173-208, 222-223`) | timeout becomes `failed` (`ros_tool_executor.py:317-319, 356-359`) | **missing** (safety-relevant) |
| Drain on shutdown | `close()` returns unfinished job ids; executor raises if any (`scheduler.py:298-324`; `parallel.py:296-303`) | none; process exits | **missing** |
| Idempotency | duplicate request id rejected; non-contiguous sequence rejected (**UNVERIFIED** at `service.py:282-295`) | none | **missing** |
| Cancellation path | agent `cancel(reason)` in contract (`agents/contracts.py:89`); mission cancel handler (`mission_state_machine.py:83`) | mission cancel checked once (`:113`); arm checks per step (`arm_skill_server.py:87`) | **partial** |
| Independent stop channel | interrupt channel with own identity (`services/environment/interrupts.py:25-51`) | `system_stop` is an agent-chosen command (`ros_tool_executor.py:144`) | **missing** (safety-relevant) |

## C. Truthfulness of results

| Capability | Upstream | Ours | Status |
|---|---|---|---|
| call-ok ≠ physical effect ≠ task success | explicit three-way split (`tools/results.py:27-74`; `toolkit.py:36-60`) | one `status` field; status 4 → `succeeded` (`ros_tool_executor.py:363`) | **missing** |
| Task success owned by environment | `task_succeeded` callback (`session.py:164`; `toolkit.py:97-106`) | true in L1 (`l1_sim.py:540-559`), absent on path A | **partial** |
| Receipt survives malformed fields | `ReceiptView` projects, never rejects (`results.py:7-25`) | no receipt object at all | **missing** |
| Invalid call leaves no effect | `no_execution` flag on schema failure (`toolkit.py:229-235`) | validation raises before dispatch (`ros_tool_executor.py:87-122`) | **present** (concept only) |
| Success verification | finish from native env, not self-report (`prepared_episode.py:90-109`) | simulated handover `succeed()` (`mission_state_machine.py:147-156`); unknown nav backend falls back to success (`nav_skill_server.py:89-115`) | **missing** |

## D. Agent runtime

| Capability | Upstream | Ours | Status |
|---|---|---|---|
| Closed-loop observe→act | agent loop + tool results per turn (`session.py:337-378`) | L1 loop only (`multi_turn_env.py:177-215`); path A is open-loop | **partial** |
| Multi-backend adapters | Codex/Claude/Gemini/managed (`agents/`) | one-shot MiniMax planner (`minimax_mission_planner.py:176-189`) | **missing** |
| Prompt assembly | layered prompt with resource files, persisted (`context/prompt.py:77-126`) | system prompt from registry (`protocol.py:191-209`) + `chat_format` | **present** (simpler) |
| History policy | atomic blocks, image placeholders, hard budget (`context/sliding_window.py`) | turn-count truncation (`multi_turn_env.py:220-232`) | **partial** |
| Code-as-policy | local HTTP bridge (`session.py:790-810`) | none | **missing** (out of scope) |
| Run artifacts | `result.json`, exchanges, artifacts (`artifacts/run.py:29-84`) | trajectory JSONL (`multi_turn_env.py:96-128`) | **partial** (no mission record) |
| Event stream | typed events + JSONL (`events/core.py:20-203`) | logging only | **missing** |

## E. Test coverage

| Area | Upstream | Ours |
|---|---|---|
| Harness / runtime | 0 tests (verified search in `00_baseline.md`) | 19 tests (`agent_harness/tests`) |
| Environment / training | 0 tests | 163 tests |
| ROS 2 packages | n/a | no ROS-side tests; `colcon test` passes vacuously |

**Our implementation is the one with evidence that it works.** This inverts the
usual assumption that the reference project is the trustworthy one.

## F. The one conflict that blocks everything

Path A validates and executes a **whole mission** (`mission_plan.schema.json`,
`ros_tool_executor.py:87-122`), while path B validates and executes **one
action per turn** (`protocol.py:78-84`, `multi_turn_env.py:177-215`).

They differ in five concrete ways:

1. **Cardinality**: N steps per model call vs exactly 1 action per turn.
2. **Validation unit**: plan-level vs action-level, with different schemas
   (9 harness actions vs 13 training actions; 5 shared names).
3. **Granularity**: `mission-control.deliver_tool` hides the entire mission
   inside one action; path B requires the model to sequence pick/verify/handover.
4. **Feedback channel**: path A has none; path B merges `StepResult` into the
   next `Observation`.
5. **Correlation unit**: `trace_id`/`mission_id` vs `episode_id`+`step_index`.

Any unification must therefore introduce a **step boundary** on path A without
deleting the plan macro that already works.
