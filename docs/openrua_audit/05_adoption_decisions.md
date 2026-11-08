# 05 — Adoption Decisions

For each upstream mechanism: **ADOPT CONCEPT** (take the idea, write our own),
**ADAPT IMPLEMENTATION** (reimplement closely, provenance required),
**REFERENCE ONLY** (read but do not bring in), **REJECT** (wrong for this
project, with a reason).

Nothing is copied in Phase 0. Licence rules are in `04_licenses.md`.

## 1. Decisions

| # | Mechanism | Upstream evidence | Decision | Reason |
|---|---|---|---|---|
| 1 | Command identity (`request_id`, monotonic `sequence`, `execution_id`) | `services/environment/contracts.py:211-216` | **ADOPT CONCEPT** | We have no way to tell a retry from a new command. Small, portable, unblocks idempotency. |
| 2 | Execution status `completed/partial/rejected/unknown` | `contracts.py:231,238` | **ADOPT CONCEPT** | Our `status` has no `unknown`; a timeout currently becomes `failed`, which is a false statement about a physical action. |
| 3 | Refuse new writes while uncertain; reconcile by reading a receipt | `client.py:173-208, 222-223` | **ADOPT CONCEPT** | This is the single most safety-relevant idea in upstream. |
| 4 | Three-way split: `call_ok` / physical effect / task success | `tools/results.py:27-74`, `toolkit.py:36-60` | **ADAPT IMPLEMENTATION** | Take the field semantics and the rule that parsing never invalidates an acknowledged action; implement in our own dataclasses. |
| 5 | Per-resource FIFO scheduling | `execution/scheduler.py:156-227` | **ADOPT CONCEPT** | Arm and base are mutually exclusive resources. Phase 2, not Phase 1. |
| 6 | Background jobs + handles + queue position | `execution/parallel.py:164-169`, `tools/client.py:17-39` | **ADOPT CONCEPT** | Needed before any long-running physical command; Phase 2. |
| 7 | Cancel queued but never mark running work cancelled | `scheduler.py:131-139` | **ADOPT CONCEPT** | Matches physical reality. |
| 8 | Drain on shutdown, report unfinished jobs | `scheduler.py:298-324`, `parallel.py:296-303` | **ADOPT CONCEPT** | Our process exits with work in flight and records nothing. |
| 9 | `EpisodeOutcome(accepted, retryable, failure_category)` with retryable ∉ denominator | `integrations/base.py:58-70` | **ADOPT CONCEPT** | Our `Outcome` enum is better at failure taxonomy but has no accepted/retryable split; take theirs as a second axis, do not replace ours. |
| 10 | Task success owned by the environment | `session.py:164`, `toolkit.py:97-106` | **ALREADY OURS** (in L1) | `l1_sim.py:540-559` already does this; the gap is path A. |
| 11 | Schema validation with `no_execution` on failure | `toolkit.py:229-235` | **ALREADY OURS** (partly) | `ActionRegistry.validate` and `validate_plan` exist; add the `no_execution` distinction. |
| 12 | Capability visibility derived from the runtime registry | `session.py:107-115` | **ADOPT CONCEPT** | Our two schema sets already drift; a capability descriptor fixes the source of truth. |
| 13 | Observation snapshots with id/version/freshness | `observation/`, snapshot manifests | **REFERENCE ONLY** (Phase 4) | Valuable, but needs camera/TF work that cannot happen in this phase. |
| 14 | Typed event stream + JSONL | `events/core.py:20-203` | **REFERENCE ONLY** | Useful for a live monitor; we can log structured JSONL first. |
| 15 | `result.json` run record | `artifacts/run.py:29-84` | **ADAPT CONCEPT** (Phase 1–2) | Path A leaves no evaluable record today. Add a small run record for missions. |
| 16 | Sliding-window history with atomic blocks | `context/sliding_window.py` | **REFERENCE ONLY** | Our L1 truncation is by turn count; revisit when missions get long. |
| 17 | Provider gateway with retry-before-delivery | `provider/client.py:156-238` | **REFERENCE ONLY** | We have no gateway; the rule "retry only before delivery" is worth remembering. |
| 18 | Multi-backend SDK adapters (Codex/Claude/Gemini) | `agents/` | **REJECT** | Drags in vendor SDK dependencies (`pyproject.toml:21-33`) for no research value here. |
| 19 | Code-as-policy HTTP bridge | `session.py:790-810` | **REJECT** | Different interaction paradigm; out of scope for the ROS 2 centre of gravity. |
| 20 | HTTP environment service | `services/environment/` | **REJECT** | ROS 2 actions/services are our transport. Adopt the *receipt contract*, not the transport. |
| 21 | Unbounded request/interrupt retention | `service.py:64,294`, `interrupts.py:12,44` | **REJECT** | Upstream keeps every receipt forever; do not copy that. |
| 22 | Inconsistent `max_turns` semantics | `agents/contracts.py:68`, `config.py:222`, `session.py:670` | **REJECT** | Define one meaning for us: model turns, per episode, explicit. |

## 2. Resolving the one-shot plan vs step loop conflict

The conflict is real and documented by our own code (`agent_env/protocol.py:3-6`).
The design must keep both workflows usable rather than pick a winner.

**Principle: one step boundary, two planners.**

```
                 ┌──────────────────────────────────────────┐
   plan macro ──▶│  submit(ActionRequest) -> ExecutionReceipt│──▶ ROS 2 Action
   (N steps)     └──────────────────────────────────────────┘
                 ┌──────────────────────────────────────────┐
   step policy ─▶│  submit(ActionRequest) -> ExecutionReceipt│──▶ same adapter
   (1 action)    └──────────────────────────────────────────┘
```

Concretely:

1. **A step is the atomic unit of execution and observation.** Every submission
   produces exactly one `ExecutionReceipt` carrying identity, status, and —
   when available — an `ObservationSnapshot`.
2. **A mission plan is a macro over steps.** `ros_tool_executor.execute_plan`
   keeps its CLI and behaviour, but each step is routed through
   `submit()`; the plan's result becomes a list of receipts instead of
   `ToolCallResult`. Failure stops the macro (today's `break`) but the receipts
   already returned remain valid evidence.
3. **Composite capabilities are allowed but must emit progress.** Path A's
   `mission-control.deliver_tool` is the only genuine composite action
   (`skills/mission-control/schema.json:4-12`). It stays a single capability,
   but the mission state machine must emit phase progress that maps onto the
   shared phase vocabulary (`protocol.py:24-48`). It is *not* decomposed in
   Phase 1.
4. **The action vocabulary is a superset, not a rename.** The 13 L1 actions stay
   exactly as they are (frozen for training). The 3 harness-only actions
   (`mission-control.deliver_tool`, `perception.recognize_openpose_gesture`,
   `perception.detect_tool_yolov8`) are registered as ROS 2-only capabilities
   in the `CapabilityDescriptor` registry, marked `available_in: ["ros2"]`.
   L1 does not gain or lose an action.
5. **Observation is the join point.** Path B already passes `last_result` into
   the next `Observation` (`protocol.py:127-141`). Path A gains the same
   structure: the receipt from step *n* becomes the observation input to step
   *n+1* when a closed-loop controller is present. The one-shot planner simply
   ignores it, which is why the macro still works.

What this buys: a model can drive either loop; the receipts are identical; the
L1 benchmark is untouched; the plan macro keeps working.

What it does not do: it does not make the current planner closed-loop. That is
Phase 5 and requires a policy loop, not just a contract.

## 3. Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| A third vocabulary | We already have 9 harness actions and 13 L1 actions; a contract with its own names would make three | The contract carries *structure* (ids, status, units); the *names* stay in the existing registries |
| Contract creep | Phase 1 could turn into a full runtime rewrite | Phase 1 is additive: new module + tests, no edits to ROS nodes or training |
| False confidence | A green contract test says nothing about a real robot | Every Phase 1 test is marked unit-level; hardware remains **NOT RUN** |
| Schema migration in L1 | Changing `agent_skills.json` silently invalidates the frozen benchmark | L1 schemas are frozen in Phase 1; a later change needs a versioned migration and a re-baseline |
| Duplicate state | Receipts could become a second mission state store | Receipts are evidence, not authority: `MissionStateMachine` keeps owning the mission |

## 4. Open questions (need a decision before Phase 1 is merged)

1. **Where does the contract live?** `agent_harness/contracts/` (runtime-side)
   vs a new top-level package. Recommendation: `agent_harness/contracts/`,
   because `agent_env/` is frozen for training and `src/` must stay ROS-only.
2. **Do we adopt `jsonschema` or hand-rolled validation?** `jsonschema` is
   already a transitive concept in `SkillRegistry` (hand-rolled) and in
   `agent_harness/tests` (schema conformance). Recommendation: `jsonschema`
   for on-disk schemas, dataclasses for in-memory types, no pydantic.
3. **Frame and unit vocabulary.** We need one canonical frame name (`map`) and
   one unit policy (metres, radians) before snapshots land. **Unresolved.**
4. **Does `partial` exist for our ROS actions?** Their service can report it;
   a ROS action result currently cannot. Phase 1 should define the value and
   Phase 3 should populate it.

## 5. Phase 1 minimal PR proposal

**Goal:** add a ROS 2-neutral, versioned step contract, with adapters and tests,
without touching behaviour.

### Files

```
agent_harness/contracts/
  __init__.py
  ids.py            EpisodeId, CommandId, SnapshotId, new_command_id()
  status.py         ExecutionStatus (not_started|queued|running|completed|
                    failed|rejected|unknown), TaskOutcomeStatus
  types.py          ActionRequest, ExecutionReceipt, ObservationSnapshot,
                    TaskOutcome, FailureCode, CapabilityDescriptor
  schema.py         JSON Schema documents for the above (single source)
  adapters.py       from_agent_env_action/observation, from_ros_action_feedback
docs/openrua_audit/contracts-v1.md
tests/test_contracts.py
```

### Contract essentials (from the master plan, Phase 1)

- `ActionRequest`: `episode_id`, `command_id`, `capability`, `arguments`,
  `requested_at`, `frame_id`, `units`, optional `state_version`.
- `ExecutionReceipt`: `command_id`, `episode_id`, `execution_status`,
  `call_ok`, `physical_effect_verified`, `started_at`, `finished_at`,
  `failure_code`, `detail`, `snapshot_id`.
- `ObservationSnapshot`: `snapshot_id`, `taken_at`, `frame_id`, `units`,
  `state_version`, payload.
- `TaskOutcome`: `success` (tri-state), `accepted`, `retryable`,
  `failure_category`.
- `CapabilityDescriptor`: name, granularity (`atomic|composite`),
  `available_in` (`l1|ros2`), required args, produced receipt fields.
- `FailureCode`: a closed enum; unknown codes are rejected at parse time.

### Tests (all runnable on this machine, no ROS 2)

1. Round-trip: each type → JSON → type, byte-stable key order.
2. Rejection: unknown unit, unknown frame, unknown failure code, negative
   timestamps.
3. Transition: `ExecutionStatus` legal transitions; `unknown` cannot become
   `completed` without a reconciliation record.
4. Backward compatibility: `agent_env.protocol.Action`/`Observation` →
   contract → back preserves the 13 action names and all fields.
5. Capability registry: every `skills/*/schema.json` action resolves to exactly
   one descriptor; the 3 ROS-only actions are not exposed to L1.
6. Schema conformance: `tests` validate the JSON Schema documents against the
   dataclasses' output.

### Non-goals in this PR

No change to `src/`, `agent_env/`, `training/`, `skills/`, `docs/results.md`.
No new runtime dependency beyond `jsonschema` (already available in the test
environment).

### Rollback

The PR adds one package and one test file. Reverting the merge removes both;
no existing module imports it yet, so there is no behavioural surface.

### Evidence to attach to the PR

`ruff check .`, `pytest` (163 + new), `pytest agent_harness/tests`, and the
statement that ROS 2 integration is **NOT RUN** in this phase.
