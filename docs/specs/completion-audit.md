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
| O5 agent gateway | done | **"model interrupted while physical job runs"** — no interruption concept exists. A model that dies mid-decision leaves no defined trace. |
| O6 evaluation alignment | **partial** | The seven named baselines: `rules` and `expert` existed; `planner_once` and the four `loop_*` conditions did not. **Built 2026-11-11** (`agent_harness/agent/conditions.py`). Also: the receipt metrics are computed only on the L2 path, not on the L1 one. |
| O7 training integration | done | — |

**Also open in this line, beyond the phase list:**

| Item | Why it is still open |
|---|---|
| Calibration for 7 of 9 families | Four have props nobody has placed; `grasp_failure` needs a slipping tool the simulated arm cannot produce; `low_confidence` and `tool_missing` need a marginal detection and an absent tool. |
| The wire-fault run | The criteria, the injector and the audit are built; the run needs a live stack and moving commands. `tc netem` works unprivileged inside `unshare -Ur -n`. |
| The L2 adapter against a live stack | The code path is verified; the ROS path is **NOT RUN** for the calibration specifically. |

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
