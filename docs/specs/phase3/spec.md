# Phase 3 Spec — Physical Execution Consistency

> **OpenRUA assimilation — Phase O3.** Not to be confused with the post-training
> line (`PT3` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R3` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

Status: **complete — merged to `main`**
Branch: to be created from `main` after approval
Master plan: [`../../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md) §Phase 3
Prerequisites: [`contracts-v1.md`](../../openrua_audit/contracts-v1.md), [`tool-runtime.md`](../../openrua_audit/tool-runtime.md)

## Goal

Make a physical command survive a lost acknowledgement: record every submission
durably, never replay an unknown-effect command, and give an operator a stop
path that does not depend on the model.

This is the highest-risk phase. It touches how the robot is allowed to move.

## The evidence gap this phase must design around

The only state surface today is `QuerySystemState`
(`src/robot_collab_interfaces/srv/QuerySystemState.srv`), which returns:

```
mission_id, state, active_nodes[], warnings[], motion_enabled
```

and the handler only fills them from the node's own memory
(`mission_state_machine.py:87-103`).

**Confirmed fact: no existing interface can answer "did command `cmd-abc`
execute?"** There is no per-command query, no goal-id lookup, and
`/mission/events` (`MissionEvent.msg`) is a transient stream, not a record.

Consequences, which drive everything below:

1. While the process lives, the action goal handle is the only reliable status
   source.
2. After a crash or restart, an `unknown` command **cannot** be resolved by
   querying. Pretending otherwise is how a robot moves twice.
3. Therefore the policy for crash orphans is fail-closed plus explicit human
   acknowledgement, not automatic reconciliation.

## What must not change

| Surface | Pinned by |
|---|---|
| Dry-run output | `tests/test_harness_dryrun_golden.py` (byte-identical) |
| The 19 harness tests | `agent_harness/tests/` |
| `agent_env/`, `training/`, `skills/` | untouched |
| `--engine legacy` | was pinned for Phase 3; **removed in O3.1** — see below |
| Contract v1 wire fields | additive only (v1.1), see §4 |

## 1. Execution ledger

Durable record of every submission, owned by the runtime.

**Storage: SQLite via stdlib `sqlite3`** (transactional, crash-safe, no new
dependency). File path from `--ledger PATH`; default `runs/ledger.db`
(already gitignored).

```sql
CREATE TABLE commands (
    command_id     TEXT PRIMARY KEY,
    episode_id     TEXT NOT NULL,
    capability     TEXT NOT NULL,
    resource_key   TEXT NOT NULL,
    goal_uuid      TEXT,            -- ROS 2 goal id, when one was created
    status         TEXT NOT NULL,   -- contract ExecutionStatus
    call_ok        INTEGER NOT NULL DEFAULT 0,
    physical_effect_verified INTEGER,          -- NULL = unknown
    failure_code   TEXT,
    detail         TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    started_at     REAL,
    finished_at    REAL,
    reconciled_by  TEXT,            -- evidence id that settled an UNKNOWN
    receipt_json   TEXT NOT NULL    -- full contract receipt, verbatim
);

CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id  TEXT NOT NULL,
    at          REAL NOT NULL,
    kind        TEXT NOT NULL,      -- submitted|started|feedback|finished|
                                    -- lost_ack|reconciled|acknowledged
    payload     TEXT NOT NULL
);
```

Rules:

- the ledger is the **record**, never the authority for mission state;
- writes happen on submit and on every terminal transition, inside one
  transaction per event;
- a duplicate `command_id` is a constraint violation, not an update.

## 2. Correlation: command id, goal uuid, receipt

| Identity | Created by | Stored in |
|---|---|---|
| `command_id` | client, before dispatch | ledger primary key, receipt |
| `goal_uuid` | ROS 2 when the goal is accepted | ledger `goal_uuid`, receipt `backend_ref` |
| `snapshot_id` | observation | receipt |

The goal uuid is captured from `goal_handle.goal_id.uuid`
(**API to verify during implementation**; rclpy exposes an action goal handle
with a `goal_id`). The client must write the mapping **before** waiting for the
result, so a crash after acceptance still leaves a trail.

Contract change (v1.1, additive): `ExecutionReceipt.backend_ref: str | None`
carries the goal uuid. Absent in v1 receipts, so old payloads still parse.

## 3. Reconciliation state machine

```
submitted ──▶ queued ──▶ running ──┬──▶ completed
                                   ├──▶ failed
                                   └──▶ unknown ──┬──▶ completed   (evidence)
                                                  ├──▶ failed      (evidence)
                                                  └──▶ (stays)     no evidence
```

Resolution rules:

| Situation | Action |
|---|---|
| timeout, goal handle still alive | query handle status; if terminal, write the real outcome; else stay `unknown` |
| cancel acknowledged, then result arrives | the result wins: `completed` is not rewritten to `cancelled` |
| process restarted with `unknown` rows | ledger marks them `orphaned_unknown`; **no automatic resolution** |
| any side-effecting submit while an `orphaned_unknown` exists | **refused** (`not_reconciled`) |
| operator runs `acknowledge-unknown --command-id X --note "..."` | ledger records `reconciled_by=operator:<name>`, command becomes `failed` with `execution_uncertain=` — still not `completed` |

The last rule is deliberately conservative: a human can clear the block, but
the ledger never claims the robot did something nobody observed.

## 4. Contract additions (v1.1, additive)

| Addition | Why |
|---|---|
| `ExecutionReceipt.backend_ref: str \| None` | correlate receipt ↔ goal uuid |
| `FailureCode.DUPLICATE_COMMAND` | a repeated command id is not an internal error |
| `FailureCode.STALE_STATE` | request based on a state version older than the adapter's view |
| `FailureCode.NOT_RECONCILED` | refused because an unknown orphan is unresolved |
| `FailureCode.STOP_TIMEOUT` | stop/cancel acknowledgement not received in the bound |

All are additive: existing v1 fields, order and meanings do not change.

## 5. ROS 2 adapter

`agent_harness/runtime/ros_adapter.py`, built on the Phase 2 `RosBackend`:

- **MultiThreadedExecutor** so `arm` and `base` can overlap for real (Phase 2
  documented this as a limitation).
- Per-resource `ReentrantCallbackGroup`; one action client per endpoint,
  reused.
- Bounded waits: `wait_for_server` and result waits use the per-command
  timeout; every wait has a deadline.
- Captures `goal_uuid` immediately after acceptance and writes it to the ledger.
- Feedback is recorded to the ledger and forwarded, never used to *change*
  terminal state.

The model never sees ROS types, goal handles, or node internals; it sees
receipts.

## 6. Stop, pause, cancel — and what is not an E-stop

Three separate paths, all callable **without** the model:

| Path | Mechanism | Bound | Effect on ledger |
|---|---|---|---|
| cancel | action `cancel_goal_async()` | 2 s ack | `failed`/`action_aborted`, or `unknown` if no answer |
| pause | `manipulation.publish_arm_control` with `arm_pause` | 1 s publish | recorded as its own command |
| stop | topic `system_stop` + adapter fail-closed flag | 1 s publish | recorded, and blocks new side-effecting submits |

**Explicitly not an E-stop.** `system_stop` is a topic message consumed by the
arm server (`arm_skill_server.py:46-58`); it is not a hardware safety circuit.
The documents and the CLI help must say so. A gesture or topic stop that the
robot's controller does not enforce is a request, not a guarantee.

The adapter's fail-closed flag is independent of the model: once
`stop`/`pause` is issued, new side-effecting submissions are refused until an
operator clears it.

## 7. Fault-injection matrix

All eight run against an in-process fake environment; none require ROS.

| # | Fault | Expected |
|---|---|---|
| 1 | ACK dropped after motion completed | `unknown`; ledger keeps goal uuid; no replay; resolves only with evidence |
| 2 | Stale snapshot / stale `state_version` | refused `stale_state`, never dispatched |
| 3 | Duplicate `command_id` | refused `duplicate_command`, one execution |
| 4 | Result dropped, handle alive | `unknown`; reconcile via handle status when it becomes available |
| 5 | Server restart between submit and result | row survives; restart marks orphaned; new side-effecting submits refused |
| 6 | Cancellation race (cancel after completion) | records `completed`; cancel returns False; no `cancelled` row |
| 7 | Delayed feedback after terminal | ignored for state, appended to events |
| 8 | Controller unavailable | `failed`/`transport_error`; **not** `unknown` (nothing was sent) |

## 8. Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| P3-R1 | Every submit is durable before dispatch | kill -9 test between submit and execute; row exists |
| P3-R2 | Duplicate command id never dispatches twice | fault #3 |
| P3-R3 | Unknown is never silently resolved | state machine tests |
| P3-R4 | An orphan blocks side-effecting work | restart test |
| P3-R5 | Cancel after completion does not rewrite history | fault #6 |
| P3-R6 | No control path requires the model | CLI tests for cancel/pause/stop |
| P3-R7 | Stop acknowledgement is bounded and its failure is visible | timeout test |
| P3-R8 | Nothing claims `physical_effect_verified=True` without an observation | review + test |
| P3-R9 | Dry-run golden and the 19 harness tests unchanged | test run |
| P3-R10 | No new runtime dependency | import check |
| P3-R11 | The ledger is not consulted as mission authority | review |
| P3-R12 | `--engine legacy` still works | superseded: the flag was **removed** in O3.1 |

## 9. Non-goals

- No change to any ROS 2 node's control logic in this phase.
- No new query service on the ROS side (see open question 2).
- No observation snapshots from cameras/TF (Phase 4); `state_version` is
  sourced from the adapter's own state reads.
- No hardware or Gazebo validation; every hardware claim stays **NOT RUN**.
- No closed-loop policy (Phase 5).

## 10. Open questions for approval

1. **Ledger storage.** Recommendation: SQLite via `sqlite3`. A JSONL file is
   easier to eyeball but has no transaction boundary, and "two records that must
   both exist" is exactly what a crash breaks.
2. **Crash orphans.** Recommendation: operator acknowledgement CLI in Phase 3;
   a ROS-side `query_command` service is a separate, later change because it
   alters the action servers. This keeps Phase 3 from editing control code.
3. **Contract v1.1 additions.** Recommendation: approve the four additive
   failure codes and `backend_ref`. They are additive and no external consumer
   exists yet.
4. **Stop semantics.** Recommendation: implement all three paths and label
   `system_stop` as a request, not an E-stop, in code comments, CLI help and
   this document. If you want a real hardware stop in scope, that is a
   controller change and needs its own phase.
5. **Where the fail-closed flag lives.** Recommendation: in the adapter, per
   process, cleared only by an explicit operator command — never by a timeout.
