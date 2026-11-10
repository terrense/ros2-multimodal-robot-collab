# Phase 2 Spec — Local Reliable Tool Runtime

> **OpenRUA assimilation — Phase O2.** Not to be confused with the post-training
> line (`PT2` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R2` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

Status: **complete — merged to `main`**
Branch: `phase2/tool-runtime` (created from `main` at `e3b7140`)
Master plan: [`../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md) §Phase 2
Phase 1 contract: [`../../openrua_audit/contracts-v1.md`](../../openrua_audit/contracts-v1.md)

## Goal

Put one model-neutral runtime between the harness and ROS 2, so that every
submission has an identity, an ordering rule and a receipt — without changing
what the existing CLIs print or what `validate_plan` accepts.

This is the first phase that modifies existing behaviour. The spec therefore
starts with what must **not** change.

## Behaviour that must not change

| Surface | Pinned by |
|---|---|
| `SkillRegistry.load` / `get_action` / `validate_plan` / `normalize_args` | `agent_harness/tests/test_harness.py:37-197` (19 tests) |
| Dry-run rendered command and JSON output | `test_harness.py:76-116`; additionally pinned by golden files captured in T1 |
| Example plans conform to `mission_plan.schema.json` | `test_harness.py:197-227` |
| `skill_router.py` remains a wrapper | `agent_harness/scripts/skill_router.py:1-9` |
| No new runtime dependency | `requirements-dev.txt` is test-only; runtime stays stdlib |

If a change needs to break one of these, it becomes a separate, versioned
decision — not a side effect of this phase.

## Architecture

```
plan macro (N steps) ─┐
                      ├─ ToolClient.submit(ActionRequest) ──▶ ExecutionReceipt
step policy (1 action)┘        │
                               ├─ ResourceScheduler  (arm | base | episode, FIFO)
                               └─ ToolBackend        (ROS 2 adapter | fake)
```

### Components

| Module | Responsibility | Owns |
|---|---|---|
| `agent_harness/runtime/client.py` | `ToolClient`: submit, wait, poll, cancel, shutdown | command lifecycle bookkeeping |
| `agent_harness/runtime/scheduler.py` | per-resource FIFO queues, queued-only cancel, drain | queue order and worker threads |
| `agent_harness/runtime/policy.py` | descriptor → resource key, exclusivity, timeout | policy derivation |
| `agent_harness/runtime/ros_backend.py` | action/service/topic dispatch behind one interface | rclpy objects |
| `agent_harness/runtime/fake_backend.py` | deterministic test double | nothing outside the process |

### Ownership boundaries

1. **ROS 2 owns sequencing inside a capability.** An accepted goal runs to its
   own conclusion; the scheduler does not slice a goal into steps. We add no
   second scheduler for action internals.
2. **The client owns ordering between capabilities.** Arm commands serialise on
   `arm`; base commands on `base`; `mission-control.deliver_tool` is exclusive on
   `episode` because it moves both.
3. **No retry.** A failed or `unknown` command is reported, never replayed. The
   absence of replay is what makes `unknown` safe to introduce.
4. **The mission state machine keeps owning mission state.** Receipts are
   evidence attached to it, never a parallel store.

## Interfaces

```python
class ToolBackend(Protocol):
    def execute(self, request: ActionRequest) -> ExecutionReceipt: ...
    def close(self) -> None: ...

class ToolClient:
    def submit(self, request: ActionRequest) -> ExecutionReceipt: ...   # queued/unknown
    def wait(self, command_id: str, timeout: float | None = None) -> ExecutionReceipt: ...
    def cancel(self, command_id: str) -> bool: ...                      # queued only
    def shutdown(self, drain_timeout: float = 30.0) -> list[str]: ...   # unfinished ids
```

`submit` returns immediately with `queued` or an immediate terminal receipt.
`wait` blocks up to the timeout and returns the current receipt; a timeout while
`running` yields `unknown`, not `failed`.

### Failure mapping (contract v1)

| Situation | ExecutionStatus | failure_code |
|---|---|---|
| capability not in registry / not on `ros2` | `rejected` | `unknown_capability` |
| arguments fail schema | `rejected` | `invalid_arguments` |
| goal refused by server | `rejected` | `action_rejected` |
| server unavailable before dispatch | `failed` | `transport_error` |
| handler raised | `failed` | `internal_error` |
| per-command timeout while running | `unknown` | `execution_uncertain` |
| ROS action aborted/cancelled | `failed` | `action_aborted` |
| ROS action succeeded | `completed` | — (`physical_effect_verified` stays `None`) |

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| P2-R1 | Dry-run output is byte-identical to the pre-refactor baseline | golden files + `test_harness.py` |
| P2-R2 | The 19 existing harness tests pass untouched | test run |
| P2-R3 | Every execution returns an `ExecutionReceipt` carrying a stable `command_id` | fake-backend tests |
| P2-R4 | Same-resource submissions execute in submission order; different resources may overlap | FIFO test with timestamps |
| P2-R5 | `cancel` returns `True` only for queued commands; a running command is left alone | test |
| P2-R6 | `shutdown` waits, cancels what is still queued, and returns unfinished ids | test |
| P2-R7 | A timeout yields `unknown`/`execution_uncertain`, never `failed` | test |
| P2-R8 | A backend exception yields `failed`/`internal_error` and leaves the client usable | test |
| P2-R9 | Per-command timeout comes from `CapabilityDescriptor.timeout_s`, falling back to a CLI default | test |
| P2-R10 | Fake-backend tests import no ROS and run in the normal `pytest` suite | test run |
| P2-R11 | `ToolClient` exposes no model-specific API (no prompt, no schema formatting) | review |
| P2-R12 | No new runtime dependency; `import agent_harness.runtime` works without rclpy | import test |

## Test matrix (the eight failure paths from the master plan)

| # | Path | Expected |
|---|---|---|
| 1 | unknown capability | `rejected` / `unknown_capability` |
| 2 | invalid arguments | `rejected` / `invalid_arguments` |
| 3 | resource collision | second command queued, not run |
| 4 | FIFO ordering | execution order equals submission order per resource |
| 5 | cancel queued | `cancelled`, never executed |
| 6 | interrupt running | `cancel` returns False; command completes or times out |
| 7 | timeout | `unknown` / `execution_uncertain` |
| 8 | exception / graceful shutdown with in-flight work | `failed`; shutdown reports the id |

## Wiring the existing CLI

`RosToolExecutor.execute_plan` keeps its signature and its `ToolCallResult`
output, but each step is routed through `ToolClient`. Rollback plan for review:

- `--engine runtime|legacy`, default `runtime`, `legacy` removed in Phase 3.
  *(Superseded: the flag was removed in O3.1. There is one execute path — the
  legacy loop dispatched with no ledger and no reconciliation, which made it the
  one way to move the robot and leave no evidence.)*
- This is the only new CLI surface in Phase 2; it exists so a bad deploy on a
  robot has a one-flag retreat.

## Non-goals

- No ledger, no reconciliation, no durable receipts (Phase 3).
- No change to `mission_state_machine` or any ROS 2 node.
- No observation snapshots, no freshness gates (Phase 4).
- No closed-loop policy loop (Phase 5).
- No change to `agent_env/` or `training/`.

## Open questions for approval

1. **Is `--engine legacy` worth the extra branch?** Recommendation: yes, for one
   phase. It doubles the execute path briefly but gives a rollback that does not
   require a revert on a robot.
2. **Where does the fake backend live?** Recommendation:
   `agent_harness/runtime/fake_backend.py`, small and pure, documented as a test
   double — a dry-run mode for Phase 3's ledger tests will want it too.
3. **Should `system_stop` bypass the queue?** Recommendation: no. A stop that
   jumps the queue is a scheduler bypass, and it is exactly the kind of
   privileged path the audit warned about; the Safety Supervisor question is
   Phase 3/6, not this phase.
4. **Per-command timeout for topics.** A topic publish has no acknowledgement,
   so a timeout is meaningless. Recommendation: map it to `completed` with
   `physical_effect_verified=None` and document that the receipt proves only
   that the publish call returned.
