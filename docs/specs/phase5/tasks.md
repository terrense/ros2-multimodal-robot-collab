# Phase 5 Tasks

> **OpenRUA assimilation — Phase O5.** Not to be confused with the post-training
> line (`PT5` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R5` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

- [x] User approves the spec and the four recommendations
- [x] `agent_harness/agent/policy.py`: `Policy` protocol, `Decide`, `Finish`, `EpisodeResult`
- [x] `agent_harness/agent/loop.py`: `ClosedLoop` (observe → decide → validate → submit → wait → observe)
- [x] `agent_harness/agent/budgets.py`: turns / characters / wall time
- [x] `agent_harness/agent/policies.py`: `ScriptedPolicy`, `StubModelPolicy`, `HumanPolicy`, `PlanMacroPolicy`
- [x] Tests: P5-R1..R8 (one action, unknown, false success, premature finish, three budgets, cancel, macro parity, no rclpy)
- [x] `docs/openrua_audit/agent-loop.md` with the loop diagram and rules
- [x] Full suite green (269 at the time; 412 now)
- [x] Report before Phase 6
