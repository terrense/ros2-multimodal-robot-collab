# Phase 2 Tasks

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## T0 — Approval gate

- [x] Write `docs/specs/phase2/spec.md`
- [ ] User approves the spec and answers the four open questions
- [ ] No implementation before that

## T1 — Freeze current behaviour

- [ ] Capture dry-run golden output for `agent_harness/examples/*.json`
      (`--json` and human form) into `agent_harness/tests/golden/`
- [ ] Add a test that reproduces the golden output exactly
- [ ] Run the 19 existing harness tests and record the result

## T2 — Runtime package

- [ ] `agent_harness/runtime/policy.py`: descriptor → resource key, exclusivity, timeout
- [ ] `agent_harness/runtime/scheduler.py`: per-resource FIFO, queued-only cancel, drain
- [ ] `agent_harness/runtime/client.py`: `ToolClient` with submit/wait/cancel/shutdown
- [ ] `agent_harness/runtime/fake_backend.py`: deterministic backend
- [ ] Import check: `import agent_harness.runtime` without rclpy

## T3 — ROS 2 backend

- [ ] `agent_harness/runtime/ros_backend.py` using `ActionClient` / service / topic
- [ ] Map every outcome to contract v1 (see spec table)
- [ ] Timeout from `CapabilityDescriptor.timeout_s`, CLI fallback
- [ ] `--engine runtime|legacy` on `ros_tool_executor`; legacy path unchanged

## T4 — Tests (fake backend only)

- [ ] The eight failure paths from the spec matrix
- [ ] FIFO ordering per resource and overlap across resources
- [ ] `cancel` returns False for running work
- [ ] `shutdown` with in-flight work returns unfinished ids
- [ ] No test imports rclpy

## T5 — Documentation and hand-off

- [ ] `docs/openrua_audit/tool-runtime.md`: ownership boundaries, why no retry,
      why `system_stop` does not bypass the queue
- [ ] Update the Phase 0 gap matrix rows that Phase 2 closes
- [ ] Record verification (ruff, pytest counts, ROS build status)
- [ ] Report before Phase 3

## Gate

- [ ] Dry-run output byte-identical (P2-R1)
- [ ] 19 harness tests untouched and green (P2-R2)
- [ ] No change to `agent_env/`, `training/`, `skills/`, `src/`
- [ ] No new runtime dependency
