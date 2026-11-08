# Contract v1

The shared step protocol between the mission-plan path and the training loop.
Code: `agent_harness/contracts/`. Tests: `tests/test_contracts.py`.

This document is the human-readable half of the contract; the dataclasses are
the executable half.

## Why it exists

At the pinned Phase 0 baseline the two paths could not describe the same event:

- the mission executor had a per-plan `trace_id` and no per-command identity;
- a timeout was recorded as `failed`, which invites a retry of a command whose
  effect is unknown;
- "the action returned SUCCEEDED" and "the tool was delivered" were the same
  field;
- the two paths validated different action vocabularies with different rules
  (`ros_tool_executor.py:87-122` vs `agent_env/protocol.py:172-189`).

Contract v1 fixes the *description*, not the runtime. Nothing was rewired in
Phase 1.

## Types

### ActionRequest

One submission.

| Field | Meaning |
|---|---|
| `episode_id` | one run of one task |
| `command_id` | idempotency identity of this submission |
| `capability` | `skill.action`, must exist in the registry |
| `arguments` | JSON object |
| `requested_at` | unix seconds |
| `frame_id` | `map`/`odom`/`base_link`/`tool0`/… or `None` |
| `units` | top-level argument name → `m`/`rad`/`s`/`m/s`/`rad/s`/`N`/`Nm`/`unitless`; a key must name an argument that exists |
| `state_version` | the client's view of robot state |
| `resource_key` | `arm`, `base` or `episode` |
| `timeout_s` | per-command budget |

### ExecutionReceipt

Evidence for one submission. **A receipt is not authority** — the mission state
machine and the L1 environment remain the owners of their state.

| Field | Meaning |
|---|---|
| `execution_status` | `not_started`/`queued`/`running`/`completed`/`failed`/`rejected`/`unknown` |
| `call_ok` | the invocation produced a well-formed result |
| `physical_effect_verified` | tri-state: the world was observed to change (or not); `None` = not established |
| `failure_code` | closed enum; required for `failed`/`rejected` |
| `snapshot_id`, `state_version` | which observation this receipt is anchored to |
| `result` | the raw payload, preserved verbatim |

Invariants enforced in `__post_init__`:

- `completed` requires `call_ok=True`.
- `not_started`, `queued`, `rejected` cannot report `call_ok=True`.
- `rejected` must state `physical_effect_verified=False` — a rejected goal
  provably had no effect.
- `unknown` defaults to `failure_code=execution_uncertain`.
- `finished_at` requires `started_at` and cannot precede it.
- Unit keys are literal top-level names, not dotted paths, and must name an
  argument that actually exists — a unit attached to a typo is rejected at
  parse time.

The published JSON Schema repeats the two invariants a non-Python reader can
check (`rejected` ⇒ `physical_effect_verified=false`, `completed` ⇒
`call_ok=true`), so a Go or TypeScript consumer cannot be more permissive than
the Python side.

### The three questions

| Field | Question | Never means |
|---|---|---|
| `call_ok` | did the call return a well-formed result? | motion, grasp, delivery |
| `physical_effect_verified` | did the world observably change? | task success |
| `TaskOutcome.success` | did the episode achieve its goal? | that the robot moved |

They can legitimately disagree. `action status 4 → COMPLETED, call_ok=True,
physical_effect_verified=None` is the normal case for a ROS 2 action.

### Status transitions

```
not_started -> queued | running | rejected
queued      -> running | rejected | unknown
running     -> completed | failed | unknown
unknown     -> completed | failed     (only with reconciled_by=<evidence id>)
terminal    -> nothing
```

`unknown` is resolved by evidence, never by assumption. `require_transition`
takes `reconciled_by` — the id of the receipt or snapshot that settled the
outcome — and raises `InvalidTransition` when it is missing or empty. A bare
boolean was rejected during review: it would let anyone resolve an unknown by
assertion, and it would leave the actual evidence nowhere.

### ObservationSnapshot, TaskOutcome, CapabilityDescriptor

- `ObservationSnapshot` carries `snapshot_id`, `taken_at`, `frame_id`,
  `state_version`, `units` and a payload. It exists so a receipt can be tied to
  the exact world state it was judged against.
- `TaskOutcome` carries `success` (tri-state), `accepted`, `retryable`,
  `failure_category`. A retryable outcome cannot be accepted, so it never
  enters the evaluation denominator.
- `CapabilityDescriptor` records granularity (`atomic`/`composite`), platforms
  (`l1`/`ros2`), required args, whether it mutates the world, and the default
  resource key.

## Adapters

`adapters.py` translates, in both directions, and imports nothing heavy:

- `request_from_agent_env(action, ...)` / `action_from_request(request)`
- `receipt_from_step_result(...)` — L1 `StepResult` → receipt. L1 `succeeded`
  maps to `COMPLETED` with `physical_effect_verified=None`: the simulator
  accepting an action is not independent verification.
- `execution_status_from_ros_goal_status(code)` — plain ints, no rclpy.
- `receipt_from_ros_action_result(payload, ...)` — never sets
  `physical_effect_verified`; an action's own success claim is not evidence of
  a physical effect.
- `receipt_for_rejected_goal(...)` — `REJECTED` with `physical_effect_verified=False`.

## Capability registry

`CapabilityRegistry.load(repo_root)` merges:

| Source | Callable actions |
|---|---|
| `skills/*/schema.json` (ROS 2) | 6 of 8 declared |
| `agent_env/schemas/agent_skills.json` (L1) | 13 |
| union | **14** (5 shared, 1 ROS-only, 8 L1-only) |

Actions declared `callable: false` are perception internals and are deliberately
absent. `registry.require(name, platform)` raises `UnsupportedCapability` when a
capability is unknown or unavailable on that platform — strict, per the master
plan.

## Extending

- New frame: `register_frame("new_frame")` before constructing snapshots.
- New unit: extend `UNIT_TOKENS` — deliberately a code change, because units are
  a physical vocabulary.
- New failure: add to `FailureCode`; never pass a free string.
- New capability: add the action to the owning schema; the registry picks it up.

## What v1 does not guarantee

- It does not make path A closed-loop. The executor does not use the contract
  yet; that is Phase 2.
- It has no ledger and no reconciliation implementation; it defines the states
  a ledger must be able to express (Phase 3).
- `physical_effect_verified` is never populated by anything today. Phase 3/4.
- It does not change `agent_env/schemas/agent_skills.json`, so the frozen
  benchmark and the 163 existing tests are untouched.
- Nothing here has been exercised against a running ROS 2 graph or hardware.
