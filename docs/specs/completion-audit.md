# Phase completion audit

What is and is not finished, per plan, with the reason. Written 2026-11-11
after the Phase 6 conditions were built.

Three plans, and each phase is checked against **the text of its own plan**,
not against whether its task boxes were ticked. A ticked box means the work
happened; it does not mean the phase delivered everything the plan asked for.

---

## OpenRUA master plan — `docs/plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`

| Phase | Code | Plan's own requirements not met |
|---|---|---|
| O0 audit | done | — |
| O1 contracts | done | — |
| O2 tool runtime | done | — |
| O3 ledger + adapter | done | — |
| O4 snapshots + skills | done | — |
| O5 agent gateway | done | — |
| O6 evaluation alignment | done | The seven named baselines are built (`agent_harness/agent/conditions.py`). Receipt metrics are computed on both levels: the L2 path from real receipts, the L1 path from the contract's own mapping applied to the recorded results — with the two things L1 cannot measure (latency, duplicate effects) reported as not applicable *with the reason* rather than as zero. The L1 side is measured and published (`docs/results.md`); the L2 side is code only — the live run has not happened. |
| O7 training integration | done | — |

**Also open in this line, beyond the phase list:**

| Item | Why it is still open |
|---|---|
| Calibration for 4 of 9 families | The other five are measured (30 samples each) against the live stack, and four of those disagree with L1. The four that are not measured are not waiting for an operator: the stack cannot produce those faults at all — the perception stub does not observe the world, nothing releases a held tool, nothing consults the handover zone, and the simulated arm never fails a grasp. Each probe tries anyway and names the mechanism that is missing. |
| The wire-fault run | The criteria, the injector and the audit are built; the run needs a live stack and moving commands. `tc netem` works unprivileged inside `unshare -Ur -n`. |
| The L2 adapter against a live stack | **Run.** Ten real missions scored by the L1 evaluator, with the receipt metrics and a run record. The L2 receipts now sit next to the L1 ones in `docs/results.md`. |

## Robot roadmap — `docs/roadmap.md`

| Phase | State |
|---|---|
| R0 scaffold | done |
| R1 single-robot lab demo | done **in simulation only** |
| R2 real hardware | **not started** — needs separate authorization, TF calibration, a working stop path |
| R3 multi-agent planning | **not started** — no viewpoint requesting, no grasp-strategy-from-geometry, no multi-robot allocation in `src/` at all |
| R4 post-training | the PT line |

## Post-training line — `EXPERIMENT_LOG.md`

| Stage | State |
|---|---|
| PT0 environment, PT1 acceptance, PT2 workspace, PT3 data integration, PT4 training stack | done as code |
| The six experiment arms | **not run** — blocked on GPU, not on code |

## The one defect that made the audit necessary

Every phase's task file had all its boxes ticked and every status line said
"complete", yet three of the plan's own acceptance requirements were missing:
the interrupted model, five of the seven baselines, and the L1 receipt metrics.

Two habits are now in place so that cannot happen again:

- `docs/specs/README.md` lists what each phase produced **and** what it did
  not;
- a phase's completion is checked against the plan text, not against its own
  checklist.
