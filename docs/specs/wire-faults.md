# Wire-fault acceptance criteria (O3.1)

The runtime handles eight transport faults in-process, against a fake backend.
That proves the state machine. It does not prove the transport: a real link can
lose a message after the robot moved and before anyone heard about it.

This file is the **acceptance criteria** for running the same scenarios over a
real DDS stack, written before the tool so the tool can be checked against it
rather than the other way round.

Implementation: [`agent_harness/runtime/wire_faults.py`](../../agent_harness/runtime/wire_faults.py)
(criteria and netem rules) and [`scripts/wire_fault_check.py`](../../scripts/wire_fault_check.py)
(apply, dispatch, restore, report).

## 1. Why safety criteria alone are not enough

A system that refuses every command satisfies every safety invariant and is
useless. So the criteria come in two kinds, and a run passes only if **both**
kinds pass:

- **safety** — what must never happen, however bad the link;
- **liveness** — what must still happen under a moderate link, or the safety
  criteria are being satisfied by doing nothing.

Every liveness criterion is scoped to a loss level at which the stack is
*expected* to make progress. At 60% loss, refusing is correct. At 15% with
bursts, never completing a mission is a bug.

## 2. The criteria

### Safety

| ID | Criterion | What it asserts | The failure it prevents |
|---|---|---|---|
| **WF-S1** | `no_duplicate_effect` | No `command_id` has more than one `started` event | A command whose result was lost gets retried and the robot moves twice |
| **WF-S2** | `uncertain_stays_unknown` | Every command whose outcome was uncertain is recorded `unknown` | Recording a timeout as `failed` is a claim that it did not happen; that claim is exactly what invites the retry |
| **WF-S3** | `blocked_until_ack` | Between an unacknowledged `unknown` and its acknowledgement, no other command is dispatched | Fail-closed is the whole point: an unresolved physical effect blocks further side effects until a human decides |
| **WF-S4** | `ledger_complete` | Every submitted command has a ledger row (terminal or `unknown`) | A command that vanished from the ledger is a command whose evidence was lost — and a lost command is indistinguishable from one that never ran |
| **WF-S5** | `no_false_success` | No receipt claims `completed` while `call_ok` is false or the effect is unverified beyond the action server's own word | Under loss, "the result arrived" and "the robot did it" are different facts; conflating them manufactures success |
| **WF-S6** | `no_unbounded_retry` | No command id is submitted more than once by the harness itself | The harness must not paper over a timeout by retrying |

### Liveness

| ID | Criterion | What it asserts | When it applies |
|---|---|---|---|
| **WF-L1** | `progress_under_moderate_loss` | At least one command reaches a terminal state other than `unknown` | Loss ≤ 20% |
| **WF-L2** | `eventually_resolves` | After the fault is cleared, a fresh command completes | Always — a stack that cannot recover from a temporary fault is broken |

### Set-up integrity

These do not test the robot; they test whether the measurement is real.

| ID | Criterion | What it asserts | The failure it prevents |
|---|---|---|---|
| **WF-C1** | `fault_was_applied` | `tc qdisc show` reports the netem rule before the first dispatch | Measuring the clean path and reporting it as the faulted path |
| **WF-C2** | `fault_was_cleared` | `tc qdisc show` reports no netem rule at the end | A leftover rule poisons every later experiment on the machine, and nobody attributes it to this tool |
| **WF-C3** | `baseline_recorded` | A clean run completed before the faulted one | Without a comparison the faulted result has no meaning: 30% success is good or bad only relative to something |

## 3. Thresholds, and why these numbers

| Fault | Loss | Correlation | Rationale |
|---|---|---|---|
| `dropped_result` | 30% | 25% | Frequent enough that losing a *result* is likely within a few runs, not so frequent that nothing completes |
| `slow_ack` | 0% | — | Delay only: 400 ms ± 120 ms sits near the default action timeout, so some commands time out without any packet being lost |
| `duplicated_packets` | 0% | — | 20% duplication with no loss: the transport duplicates, the application must not |
| `bursty_loss` | 15% | 75% | The realistic case. Independent loss is easier than what a real link does |
| `severe` | 60% | 50% | Most commands must end `unknown`; nothing may end `failed` for a timeout |

Correlation matters more than the rate. Independent loss at 15% is survivable by
retrying a single packet; 15% correlated loss removes a whole exchange.

## 4. What counts as a failure of the experiment, not of the system

If a set-up criterion fails, the run is **void**: it says nothing about the
robot either way. Reporting a void run as a system failure is its own kind of
lie, and it is the common one — a misconfigured injection looks exactly like a
system that survived a fault it never saw.

So the verdict has three states, not two:

| Verdict | Meaning |
|---|---|
| **PASS** | All safety, all liveness, all set-up criteria hold |
| **FAIL** | A set-up criterion holds and a safety or liveness criterion does not — the system is at fault |
| **VOID** | A set-up criterion failed — the measurement is not valid; fix the harness and repeat |

## 5. What these criteria do not cover

- **No hardware.** Everything here is Gazebo/simulated. A real link behaves
  differently, and a real robot has inertia.
- **No port filtering by default.** CycloneDDS picks ports dynamically, so a
  filter can silently miss traffic, and a filter that misses traffic produces a
  green result that proves nothing. The default covers the whole loopback
  interface.
- **Post-hoc, not live.** The audit reads final statuses and the event order
  after the run. It answers "did the system double-apply a command", not "is it
  about to".
- **No clock skew.** A single machine has one clock; two machines under loss
  would need the timestamps reconciled first.

## 6. Status

| | |
|---|---|
| Criteria | **written** (this file) |
| Netem rules and command construction | **implemented and tested** |
| Ledger audit for WF-S1..S6, WF-L1 | **implemented and tested** |
| Set-up probes WF-C1..C3 | **implemented and tested** against a fake probe |
| A real run | **NOT RUN** — needs root, a live stack, and a person |

The one thing that cannot be faked is the last row: whether CycloneDDS itself
survives a lossy interface is a fact about a running system.
