# Agent Loop (Phase 5)

One policy interface, one loop, receipts on every turn.

```
observe  (fresh snapshot, evaluator fields stripped)
   │  stale/absent → wait up to max_observe_wait_s → no_observation
   ▼
policy.decide(view) ── Finish ──▶ verify the claim against the environment
   │ Decide (exactly one)
   ▼
registry.require(capability)  → invalid_capability
   │
   ▼
client.submit → wait → ExecutionReceipt
   │  unknown → stop, execution_uncertain (never resubmitted)
   ▼
policy.consume(receipt) → next view
```

## Rules the loop enforces

| Rule | Failure code |
|---|---|
| one action per decision | `invalid_decision` |
| unknown effect is final for the episode | `unknown_outcome` (`execution_uncertain`) |
| a success claim needs environment confirmation | `false_success` |
| finishing without a terminal receipt | `premature_finish` |
| turn / character / wall budgets | `budget_turns`, `budget_tokens`, `budget_wall` |
| cancellation before submit | `cancelled` (running work untouched) |
| no fresh observation | `no_observation` |

## Backends

| Policy | Purpose |
|---|---|
| `ScriptedPolicy` | fixed decision list (baseline) |
| `StubModelPolicy` | deterministic model stand-in; counts characters as the token budget |
| `HumanPolicy` | callback-driven debugging |
| `PlanMacroPolicy` | a validated mission plan replayed one step per decision |

Claude/Codex adapters are deliberately absent: the `Policy` protocol is the
seam they will plug into, and nothing in `agent_harness/agent/` imports rclpy.

## Tests

`tests/test_agent_loop.py` covers P5-R1..P5-R8 with the fake backend; the plan
macro is compared against the step loop for receipt parity. 13 tests.
