# Local Tool Runtime (Phase 2)

Code: `agent_harness/runtime/`. Tests: `tests/test_tool_runtime.py`,
`tests/test_ros_backend_mapping.py`, `tests/test_harness_dryrun_golden.py`.

## What it is

One client between the harness and ROS 2. A caller submits an
`ActionRequest`; it gets an `ExecutionReceipt`. The client does not know what a
prompt is, and it does not know how a ROS 2 message is encoded.

```
plan macro ──┐
             ├─ ToolClient.submit(request) ──▶ receipt
step policy ─┘        ├─ per-resource FIFO scheduler (arm | base | episode)
                      └─ backend (ROS 2 adapter | fake)
```

## Ownership boundaries

| Question | Owner | Why |
|---|---|---|
| Ordering **inside** one capability | ROS 2 action server | We do not slice a goal into steps; that would be a second scheduler for the same work |
| Ordering **between** capabilities | `ResourceScheduler` | Two arm commands must not run at once; arm and base may |
| Whether a command succeeded | the environment / action result | `call_ok` and `physical_effect_verified` are separate fields |
| Mission state | `MissionStateMachine` | Receipts are evidence attached to it, never a parallel store |
| Nothing | retries | See below |

## Why there is no retry

A retry is only safe when the previous attempt provably did not execute. Our
transport cannot prove that after a timeout: a goal that timed out may still be
running. So the runtime reports `unknown` and stops. Resolving an `unknown`
requires reading evidence (Phase 3's ledger), not resubmitting.

This is also why `cancel` refuses to touch running work: marking a running
command cancelled would make the ledger believe the robot stopped.

## Why `system_stop` does not bypass the queue

A stop that jumps the queue is a scheduler bypass, and every bypass is a place
where the ordering guarantee silently stops holding. Emergency behaviour is a
controller/hardware responsibility (Safety Supervisor, Phase 3/6), not a
privileged tool call from the model. The `emergency` flag derived from
`command == "system_stop"` in `normalize_args` is therefore not treated as a
safety mechanism — it is a payload field.

## Backends

- `RosBackend` delegates to the existing `RosToolExecutor` methods. It adds no
  dispatch logic of its own; a source of drift between dry-run and execute is
  exactly what this phase is trying to remove.
- `FakeBackend` is deterministic: declared outcomes, recorded call order and
  in-flight intervals. Every failure path is tested through it.

## Known limitations (deliberate, Phase 3 scope)

1. **The ROS backend serialises dispatch through one node.** Cross-resource
   overlap is proved by the fake backend; making it true on ROS needs a
   `MultiThreadedExecutor` and belongs to the Phase 3 adapter.
2. **No ledger.** Receipts live in memory and die with the process. A crash
   after a physical command loses the evidence, not the motion.
3. **`physical_effect_verified` is always `None`.** Nothing observes the world
   yet; Phase 3/4 will populate it.
4. **`--engine legacy` still exists.** It is the one-phase retreat hatch on
   `ros_tool_executor`; Phase 3 removes it.

## Failure mapping

| Situation | Status | Code |
|---|---|---|
| unknown capability | rejected | `unknown_capability` |
| missing arguments | rejected | `invalid_arguments` |
| goal refused | rejected | `action_rejected` |
| queued command cancelled | rejected | `cancelled` |
| server unavailable | failed | `transport_error` |
| backend raised | failed | `internal_error` |
| action aborted | failed | `action_aborted` |
| timeout while running | unknown | `execution_uncertain` |
| action succeeded | completed | — (`physical_effect_verified=None`) |

## Verification at the end of Phase 2

```
ruff check .                         All checks passed
pytest                               225 passed, 3 skipped
  of which runtime                  13
  of which ROS mapping               8
  of which dry-run golden            4  (byte-identical)
pytest agent_harness/tests           19 passed
ROS 2 build                          NOT RUN on this branch (no src/ change)
hardware / Gazebo                    NOT RUN
```
