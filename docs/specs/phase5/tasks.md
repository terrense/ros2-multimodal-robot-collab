# Phase 5 Tasks

- [ ] User approves the spec and the four recommendations
- [ ] `agent_harness/agent/policy.py`: `Policy` protocol, `Decide`, `Finish`, `EpisodeResult`
- [ ] `agent_harness/agent/loop.py`: `ClosedLoop` (observe → decide → validate → submit → wait → observe)
- [ ] `agent_harness/agent/budgets.py`: turns / characters / wall time
- [ ] `agent_harness/agent/policies.py`: `ScriptedPolicy`, `StubModelPolicy`, `HumanPolicy`, `PlanMacroPolicy`
- [ ] Tests: P5-R1..R8 (one action, unknown, false success, premature finish, three budgets, cancel, macro parity, no rclpy)
- [ ] `docs/openrua_audit/agent-loop.md` with the loop diagram and rules
- [ ] Full suite green (269+)
- [ ] Report before Phase 6
