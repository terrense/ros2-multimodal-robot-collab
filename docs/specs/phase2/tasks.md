# Phase 2 Tasks

> **OpenRUA assimilation — Phase O2.** Not to be confused with the post-training
> line (`PT2` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R2` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

Status legend: `[ ]` pending · `[~]` in progress · `[x]` done · `[!]` blocked

## T0 — Approval gate

- [x] Write `docs/specs/phase2/spec.md`
- [x] User approved the spec and the four recommendations
- [x] Implementation started only after approval

## T1 — Freeze current behaviour

- [x] Capture dry-run golden output for `agent_harness/examples/*.json`
      (`--json` and human form) into `agent_harness/tests/golden/`
- [x] Add a test that reproduces the golden output exactly
- [x] Run the 19 existing harness tests and record the result

## T2 — Runtime package

- [x] `agent_harness/runtime/policy.py`: descriptor → resource key, exclusivity, timeout
- [x] `agent_harness/runtime/scheduler.py`: per-resource FIFO, queued-only cancel, drain
- [x] `agent_harness/runtime/client.py`: `ToolClient` with submit/wait/cancel/shutdown
- [x] `agent_harness/runtime/fake_backend.py`: deterministic backend
- [x] Import check: `import agent_harness.runtime` without rclpy

## T3 — ROS 2 backend

- [x] `agent_harness/runtime/ros_backend.py` using `ActionClient` / service / topic
- [x] Map every outcome to contract v1 (see spec table)
- [x] Timeout from `CapabilityDescriptor.timeout_s`, CLI fallback
- [x] `--engine runtime|legacy` on `ros_tool_executor`; legacy path unchanged

## T4 — Tests (fake backend only)

- [x] The eight failure paths from the spec matrix
- [x] FIFO ordering per resource and overlap across resources
- [x] `cancel` returns False for running work
- [x] `shutdown` with in-flight work returns unfinished ids
- [x] No test imports rclpy

## T5 — Documentation and hand-off

- [x] `docs/openrua_audit/tool-runtime.md`: ownership boundaries, why no retry,
      why `system_stop` does not bypass the queue
- [x] Update the Phase 0 gap matrix rows that Phase 2 closes
- [x] Record verification (ruff, pytest counts, ROS build status)
- [x] Report before Phase 3

## Gate

- [x] Dry-run output byte-identical (P2-R1)
- [x] 19 harness tests untouched and green (P2-R2)
- [x] No change to `agent_env/`, `training/`, `skills/`, `src/`
- [x] No new runtime dependency
