# Phase 0 Spec — Two-Repository Audit and Migration Design

Status: **in progress**
Branch: `phase0/two-repo-audit`
Master plan: [`../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md`](../plans/CLAUDE_OPENRUA_INTEGRATION_MASTER_PLAN.md)

## Goal

Produce a source-backed, reproducible audit of two repositories and a design
that lets this project absorb the engineering mechanisms of OpenRUA **without**
replacing the ROS2 execution graph, the L1 training environment, or the
post-training loop.

This phase produces documents only. No implementation code changes.

## Requirements

| ID | Requirement | Acceptance |
|---|---|---|
| R1 | Pin both repositories to exact commit SHAs | Recorded in `00_baseline.md`, with branch, worktree state and clone command |
| R2 | Read real code of the target, not READMEs | Every claim in `02_target_callgraph.md` cites path + symbol + line range |
| R3 | Trace OpenRUA in its actual runtime order | CLI → config → session → agent → toolkit → executor → scheduler → integration → environment → artifacts, in `01_upstream_callgraph.md` |
| R4 | Per-module analysis | Responsibility, interface, key classes, call relations, state ownership, error handling, strengths, defects, test coverage |
| R5 | Gap matrix against our code | Each row classified: already present / missing / duplicated / incompatible |
| R6 | No wholesale copying, no assumption of upstream superiority | Every upstream module gets ADOPT CONCEPT / ADAPT IMPLEMENTATION / REFERENCE ONLY / REJECT, with reasoning |
| R7 | Resolve the one-shot Mission Plan vs multi-turn L1 conflict | A concrete unification proposal that keeps both workflows working |
| R8 | Protect existing assets | 400-episode frozen benchmark, data splits, training scripts, ROS2 run flows must be untouched by the proposed design |
| R9 | Evidence, not assertion | Confirmed facts separated from hypotheses; line numbers included |
| R10 | Produce audit docs, callgraph, decision matrix, Phase 1 minimal PR plan | All six documents exist under `docs/openrua_audit/` |

## Constraints

- Read-only with respect to both repositories' code. The only tracked changes in
  this phase are under `docs/` (and agent/spec scaffolding).
- No hardware motion, no Gazebo run, no LLM calls in this phase.
- Anything not executed is marked **NOT RUN**, never **PASS**.
- OpenRUA is cloned outside this repository; it is reference material, not a
  submodule and not vendored code.
- Licence review is a blocking gate for any later code reuse.

## Deliverables

```
docs/openrua_audit/
  00_baseline.md          pinned SHAs, inventory, licence, baseline test output
  01_upstream_callgraph.md
  02_target_callgraph.md
  03_gap_matrix.md
  04_licenses.md
  05_adoption_decisions.md
```

Plus, in `tasks.md`, the Phase 1 minimal PR proposal and its open questions.

## Non-goals

- Implementing the contract package (Phase 1).
- Refactoring `agent_harness` (Phase 2).
- Any change to `src/robot_collab_*`, `agent_env/`, `training/`, `skills/`,
  `docs/results.md`.
