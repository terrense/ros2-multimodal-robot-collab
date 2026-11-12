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
| **WF-S2** | `uncertain_stays_unknown` | Every command whose outcome was uncertain was recorded `unknown`; only an operator's acknowledgement may move it on, and then only to `failed`, never to `completed` | Recording a timeout as `failed` is a claim that it did not happen; that claim is exactly what invites the retry |
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
| **WF-C4** | `fault_reached_the_transport` | The link's own counters show the fault was in play: packets dropped, for a loss; measurably more time, for a delay. A fault that only duplicates packets drops none and costs no time — there the ledger is the evidence | A run in which the loss never reached a packet passes every other criterion: nothing was uncertain, nothing was duplicated, and the recovery worked — because the link was healthy. The first two real runs came back PASS exactly that way |

## 3. Thresholds, and why these numbers

| Fault | Loss | Correlation | Rationale |
|---|---|---|---|
| `dropped_result` | 30% | 25% | Frequent enough that losing a *result* is likely within a few runs, not so frequent that nothing completes |
| `slow_ack` | 0% | — | Delay only: 400 ms ± 120 ms sits near the default action timeout, so some commands time out without any packet being lost |
| `duplicated_packets` | 0% | — | 20% duplication with no loss: the transport duplicates, the application must not |
| `bursty_loss` | 30% | 50% | The realistic case. Independent loss is easier than what a real link does |
| `severe` | 60% | 50% | Most commands must end `unknown`; nothing may end `failed` for a timeout |
| `blackout` | 95% | 50% | Loss high enough that retransmission cannot cover it: the command times out, the outcome stays `unknown`, and the next command is refused until an operator acknowledges |

**These numbers were measured, not assumed.** The first version of the table
said `bursty_loss` was 15% at 75% correlation, and it was: the link reported
**0 packets dropped out of 2000**. A high correlation argument does not make
the loss burstier, it makes it disappear. The rule now reads 30% at 50%, which
drops 16% of packets, and the criterion caught the difference — the first run
of the mis-specified scenario came back VOID rather than a false PASS.

| Rule | Dropped |
|---|---|
| `loss 15% 75%` | 0 of 2000 |
| `loss 20% 75%` | 1 of 1999 |
| `loss 25% 75%` | 27 of 1973 |
| `loss 30% 25%` | 476 of 1524 (31%) |
| `loss 30% 50%` | 276 of 1724 (16%) |
| `loss 40% 25%` | 728 of 1272 (57%) |

**A fault has to show up in the link's own numbers.** DDS retransmits a lost
packet, so a reliable transport survives loss by taking longer, not by failing.
The direct evidence is better than a proxy: `tc -s qdisc show` prints how many
packets the link dropped, and a packet the link says it dropped was dropped.

So the question each scenario asks is different, and the criterion asks the
right one:

| Fault | What the criterion asks |
|---|---|
| loss | did the link report dropping packets? |
| delay | did the dispatch cost measurably more time — 0.25 s or a tenth of the baseline, whichever is more? |
| duplication | nothing: duplication drops none and costs no time. The ledger is the evidence — a duplicated frame must not become a duplicated command |

This is the criterion that catches the failure the first real runs made — see
below.

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
| netem without root | **verified** — `unshare -Ur -n` grants CAP_NET_ADMIN over its own namespace |
| A real run | **NOT RUN** — needs a live stack, and the commands move the robot |

The one thing that cannot be faked is the last row: whether CycloneDDS itself
survives a lossy interface is a fact about a running system.

## 7. Running it without sudo

`tc` needs CAP_NET_ADMIN over the interface. A **user namespace** grants that
over the interfaces inside it, so the whole experiment can run unprivileged:

```bash
unshare -Ur -n bash -c '
  ip link set lo up
  tc qdisc add dev lo root netem loss 30%
  # ... start the stack and run the check inside this namespace ...
  tc qdisc del dev lo root
'
```

Verified on this machine: inside `unshare -Ur -n`, `tc qdisc add` succeeds and
the rule shows up in `tc qdisc show`. Outside it, `RTNETLINK answers:
Operation not permitted`.

The catch is that **everything must be inside the one namespace**: the nodes,
the tool client and the injector. A stack started outside it has its own
loopback and the fault never reaches it. So the runner is:

```bash
unshare -Ur -n bash -c 'scripts/run_gazebo_demo.sh --headless; \
                        scripts/wire_fault_check.py bursty_loss'
```

A loopback-only interface also has no multicast route by default, which is how
DDS finds peers. `ip link set lo up` is enough for the single-machine case —
all the nodes are on the same host — but it is worth knowing if discovery
behaves differently than it does on the normal interface.

### Two things that bite inside the namespace

Both were found by running it, and both stop the stack from coming up at all.

**The loopback needs its MULTICAST flag.** A namespace's `lo` has no MULTICAST
flag by default. CycloneDDS reports it as *"selected interface lo is not
multicast-capable: disabling multicast"*, and then cannot find a free
participant index for the domain — every node dies at creation with
`failed to find a free participant index for domain N`. `ip link set lo
multicast on` fixes it, and the whole stack comes up.

**The stack needs a domain of its own.** The DDS participant index is booked
per domain, and the machine's own stack already holds the indices for domain
0. A different `ROS_DOMAIN_ID` uses different ports and different locks.

```bash
unshare -Ur -n bash -c '
  ip link set lo up
  ip link set lo multicast on
  ip addr add 127.0.0.1/8 dev lo
  ip route add 224.0.0.0/4 dev lo
  export ROS_DOMAIN_ID=42
  # ... start the stack and run the check here ...
'
```

Cross-process discovery inside the namespace was verified before the stack was
started: two separate processes, one publishing and one subscribing, over the
namespace's loopback.

### The probe has to read the whole tree

The rule is installed as a `prio` root qdisc with the netem on band `1:3`.
`tc qdisc show dev lo root` prints the prio band and nothing else, so a probe
that asks for the root cannot see the rule it is supposed to confirm.

The first real run of this experiment came back **VOID** for exactly that
reason: the fault had been injected, the faulted dispatch had gone out, and the
probe reported "no netem rule was observed before the faulted dispatch". The
run was correctly refused — the verdict was void, not pass — but the void was
the *measurement's* fault, not the system's. The probe reads the whole tree
now (`tc qdisc show dev lo`), and there is a test that pins it.

## 8. What the runs showed

The stack, the injector and the audit all run inside one user+network
namespace, so the whole experiment is unprivileged and the fault cannot reach
anything outside it.

| Scenario | Fault | Baseline | Faulted | Link counters | Verdict |
|---|---|---|---|---|---|
| `dropped_result` | 30% loss, 25% correlation | 2.24 s | 2.63 s | 804 sent, **250 dropped** | **PASS** |
| `slow_ack` | 400 ms ± 120 ms delay | 2.22 s | 3.96 s | 2090 sent, 0 dropped | **PASS** |
| `duplicated_packets` | 20% duplication | 2.20 s | 2.08 s | 488 sent, 0 dropped | **PASS** |
| `bursty_loss` | 30% loss, 50% correlation | 2.14 s | 2.11 s | 717 sent, **87 dropped** | **PASS** |
| `severe` | 60% loss, 50% correlation, 200 ms | 2.21 s | **`unknown` after 30.05 s** | 3264 sent, **7699 dropped** | **PASS** |
| `blackout` | 95% loss, 50% correlation | 2.17 s | **`unknown` after 30.04 s** | 0 sent, **1221 dropped** | **PASS** |

All six pass all twelve criteria. Three of them are worth reading closely.

**`severe` is the transport doing its job — right up to the edge.** Under 60%
loss with half of it correlated, the dispatch came back `unknown`: the
command timed out, because the link dropped 7699 packets while sending 3264.
An earlier run of the same scenario *completed*, in 17.5 s against a healthy
2.2 s — eight times slower, because DDS retransmits what the link drops. So
60% loss sits right where the transport stops absorbing it, and which side of
that line a run lands on is not decided by the rule alone.

**`blackout` is the fail-closed path.** At 95% loss nothing survives: the
command timed out, and its outcome was **`unknown`** — not `failed`. The
ledger blocked every later side effect until an operator acknowledged it, and
only then did the recovery dispatch go out. That is the whole contract in one
run: an unestablished effect is not a failure, it is an open question, and a
person has to close it.

**The milder scenarios say nothing was uncertain.** In the four runs above 60%
loss, nothing ended `unknown`, so "an uncertain outcome stays unknown" and
"nothing is dispatched while an unresolved command is unacknowledged" had
nothing to check. `severe` and `blackout` exist to give them something to
check.

### The two things these runs found in the harness

**The probe could not see the fault it was confirming.** The rule is a `prio`
root qdisc with the netem on band `1:3`, and the probe asked `tc qdisc show
dev lo root` — the prio band and nothing else. The first real run came back
VOID for that reason.

**The criteria could not tell "survived the fault" from "the fault never
arrived".** A run in which the loss never reached a packet passes every
criterion: nothing uncertain, nothing duplicated, the recovery fine — because
the link was healthy. The first two real runs came back PASS exactly that
way, at 30% and at 60% loss. Hence WF-C4, and hence the link's own counters:
the fault is now confirmed by the number of packets the link says it dropped.

**And the criteria disagreed with the ledger.** `blackout` came back FAIL at
first, because the ledger records an acknowledged orphan as `failed` — by
design — while WF-S2 said a command whose outcome was uncertain must never be
recorded `failed`. Both were right about different moments: the ledger's
`failed` is the operator's decision *after* the fact, and the receipt it
stored still says `unknown`. The criterion now reads what was recorded while
the command was uncertain, and allows an acknowledgement to move it on — to
`failed`, never to `completed`.
