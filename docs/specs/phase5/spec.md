# Phase 5 Spec — Agent Gateway and the Closed Loop

Status: **draft — awaiting approval**
Master plan: [`../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md) §Phase 5
Depends on: contract v1.2, `ToolClient` (Phase 2), ledger and fail-closed gate
(Phase 3), `SnapshotBuilder` / `model_view` (Phase 4).

## Goal

One policy interface and one loop. A policy — scripted baseline, a model
backend, or a human debugging driver — observes a snapshot, picks exactly one
capability, and receives the receipt on the next turn. The one-shot mission plan
stays supported as a macro over that loop.

## What already exists

Phase 2–4 built every piece the loop needs; Phase 5 composes them and adds the
missing decisions:

| Piece | Where | Role in the loop |
|---|---|---|
| `ToolClient.submit/wait/cancel` | `agent_harness/runtime/client.py` | execute one action, produce a receipt |
| `ResourceScheduler`, fail-closed | `runtime/scheduler.py`, `runtime/client.py` | ordering and safety gates |
| `ExecutionLedger` | `runtime/ledger.py` | durable evidence, orphan blocking |
| `SnapshotBuilder`, `model_view` | `runtime/snapshots.py` | observation with freshness and hidden-field stripping |
| `CapabilityRegistry` | `contracts/capabilities.py` | validation and model-visible list |
| mission plan macro | `agent_harness/scripts/ros_tool_executor.py` | optional pre-decided sequence |

## Interfaces

```python
class Policy(Protocol):
    name: str
    def reset(self) -> None: ...
    def decide(self, view: dict) -> Decision: ...
    def consume(self, receipt: dict) -> None: ...   # what happened
    def tokens_used(self) -> int: ...

@dataclass
class Decide:  capability: str; arguments: dict; reason: str = ""
@dataclass
class Finish:  claimed_success: bool; reason: str = ""
Decision = Decide | Finish

class ClosedLoop:
    def __init__(self, policy, client, registry, snapshots, *, session, budgets) -> None
    def run(self, task: str) -> EpisodeResult: ...
    def cancel(self, reason: str) -> None: ...
```

### Loop semantics

```
observe (fresh snapshot, hidden fields stripped)
   │  no fresh snapshot → wait, bounded by budget → finish(unverified)
   ▼
policy.decide(view)
   │  Decide → validate against registry → submit → wait → receipt
   │           receipt becomes next view's last_receipt
   │  Finish → verify against environment outcome (never trust the claim)
   ▼
budget check (turns, tokens, wall time) → finish(budget_exhausted)
```

Hard rules carried over from earlier phases:

1. **One action per decision.** A policy cannot emit a list.
2. **Unknown is not failure and is never retried.** An `unknown` receipt stops
   the loop with `execution_uncertain` unless the policy explicitly chooses a
   recovery that does not resubmit the same `command_id`.
3. **Task success comes from the environment.** `Finish.claimed_success` is
   recorded as a claim; `EpisodeResult.success` comes from the integration /
   ledger outcome. A claim without evidence is `false_success`.
4. **Cancellation does not stop physical work.** `ClosedLoop.cancel()` requests
   policy cancellation; running commands are left to the runtime’s cancel path
   and reported, never silently marked stopped.
5. **Premature finish is a finding.** Finishing with a claim while the last
   receipt is non-terminal or unknown yields `premature_finish`.

### Budgets

| Budget | Unit | Enforcement |
|---|---|---|
| turns | decisions | hard stop before submit |
| tokens | policy-reported | checked before each decide; a policy that cannot count reports 0 |
| wall time | seconds | checked before each decide and while waiting |

Budgets are per episode and reported in `EpisodeResult.budgets`.

### Backends in this phase

- `ScriptedPolicy` — a list of decisions (the existing baseline, testable).
- `StubModelPolicy` — deterministic stand-in for a model backend; counts tokens
  as characters and exercises the retry/token paths without a network.
- `HumanPolicy` — callback-driven (test harness calls it); no CLI yet.

Claude/Codex adapters and shared prompt machinery are explicitly **out of
scope** in this phase; the `Policy` interface is the seam they will land behind.

### Mission plan as a macro

`PlanMacroPolicy` wraps an existing validated plan (`SkillRegistry.validate_plan`
output) and emits its steps one per decision. The existing
`ros_tool_executor --mode execute` path keeps working unchanged; Phase 5 only
adds the macro as a policy so both loops share receipts and the ledger.

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| P5-R1 | One action per decision; a list is rejected | test |
| P5-R2 | Unknown receipts stop the loop and are never resubmitted | test |
| P5-R3 | `claimed_success` never becomes `success` without environment evidence | test |
| P5-R4 | Finishing with a non-terminal last receipt is `premature_finish` | test |
| P5-R5 | Turn/token/time budgets stop the loop with a recorded reason | three tests |
| P5-R6 | `cancel()` does not mark running work stopped | test |
| P5-R7 | A plan can run as a macro, producing the same receipts as the step loop | test |
| P5-R8 | Loop core imports no rclpy | import test |
| P5-R9 | 268 existing tests stay green | test run |
| P5-R10 | No new runtime dependency | requirements-dev only |

## Non-goals

- No Claude/Codex SDK adapters, no provider network code.
- No change to ROS 2 nodes or the L1 vocabulary.
- No change to the frozen benchmark.

## Open questions for approval

1. **Loop location.** Recommendation: `agent_harness/agent/` (policies + loop),
   keeping `agent_harness/runtime/` as the execution layer.
2. **Token accounting for stub policies.** Recommendation: character count with
   an explicit `token_budget_chars` naming, so nobody mistakes it for real
   tokens; real backends report their own count later.
3. **Human driver.** Recommendation: callback-based in this phase; a stdin CLI
   driver is a later, separate step.
4. **EpisodeResult location.** Recommendation: reuse the contract’s
   `TaskOutcome` inside a small `EpisodeResult` dataclass rather than a new
   outcome type.
