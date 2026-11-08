# Phase 0 Tasks

Living checklist. Every completed step gets a commit reference, so the history
and this file tell the same story.

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## T0 — Setup

- [x] Import master plan into `docs/plans/` (sha256 `F503178C…078F21`)
- [x] Create branch `phase0/two-repo-audit`
- [x] Write `docs/specs/phase0/spec.md`
- [ ] Define project subagents under `.claude/agents/`

## T1 — Baseline (`00_baseline.md`)

- [ ] Record target repo state: branch, HEAD SHA, worktree, remote
- [ ] Inventory tracked files, packages, tests, dependencies, launch entrypoints
- [ ] Clone OpenRUA outside the repo and pin its HEAD SHA
- [ ] Record upstream inventory (top-level modules, test dirs, licence)
- [ ] Capture baseline checks: `ruff`, `pytest`, `pytest agent_harness/tests`,
      ROS2 colcon build evidence (CI run id or container output)
- [ ] Record what is NOT RUN and why

## T2 — Upstream callgraph (`01_upstream_callgraph.md`)

- [ ] CLI and config entry
- [ ] Session assembly and component wiring
- [ ] Agent adapter and provider abstraction
- [ ] Toolkit, tool schema and capability declaration
- [ ] Executor and scheduler (concurrency, cancellation, ordering)
- [ ] Integrations (scene / motion / provider)
- [ ] Environment client, service, receipt
- [ ] Backend, artifacts, finalization, telemetry
- [ ] Per-module table: responsibility, interface, state ownership, errors,
      strengths, defects, test coverage

## T3 — Target callgraph (`02_target_callgraph.md`)

- [ ] Path A: `minimax_mission_planner` → `SkillRegistry.validate_plan` →
      `ros_tool_executor` → ROS2 ActionClient → `mission_state_machine` →
      nav/manipulation actions → feedback/cancel
- [ ] Path B: policy → `MultiTurnDeliveryEnv.step` → `L1DeliveryEnv.step` →
      reward → trajectory record
- [ ] Path C: `agent_env/protocol.py` action/observation contract and its
      relationship to `training/chat_format.py`
- [ ] State ownership map: who owns mission state, robot state, episode state
- [ ] Test coverage map per module

## T4 — Gap matrix (`03_gap_matrix.md`)

- [ ] One row per capability: upstream mechanism vs our implementation
- [ ] Classify: present / missing / duplicated / incompatible
- [ ] Flag the one-shot mission plan vs multi-turn step conflict with evidence

## T5 — Licences (`04_licenses.md`)

- [ ] Upstream licence + transitive obligations
- [ ] Our licences and `third_party/` obligations (AGPL/GPL/noncommercial)
- [ ] Rule for what can and cannot be copied

## T6 — Adoption decisions (`05_adoption_decisions.md`)

- [ ] Decision table: ADOPT CONCEPT / ADAPT IMPLEMENTATION / REFERENCE ONLY /
      REJECT, one row per mechanism
- [ ] Unification design for mission-plan vs multi-turn
- [ ] Risks, open questions, explicit hypotheses marked as such
- [ ] Phase 1 minimal PR proposal with test plan and rollback

## Gate

- [ ] `git status` shows no tracked changes outside `docs/` and `.claude/`
- [ ] Baseline checks re-run and recorded
- [ ] Phase 1 PR proposal reviewed before any implementation
