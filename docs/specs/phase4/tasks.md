# Phase 4 Tasks

> **OpenRUA assimilation — Phase O4.** Not to be confused with the post-training
> line (`PT4` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R4` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

- [x] Spec (`docs/specs/phase4/spec.md`)
- [x] Snapshot identity: `sensor_id`, `calibration_version`, frame, state version
- [x] Freshness gate (`is_fresh` / `require_fresh`), future snapshots rejected
- [x] `CapabilityRegistry.describe_for(platform)` — model-facing list derived from the registry
- [x] `SnapshotBuilder` + `model_view` (evaluator-only `_` fields stripped)
- [x] Live ROS snapshot publisher (`robot_collab_slam/snapshot_publisher_node.py`):
      640x480 image metadata + `map -> camera_link` pose at 5 Hz, verified on the
      running simulation (first frames without TF are dropped, not published)
- [x] Full suite: 268 passed, 3 skipped
- [x] Skills layering document (general-purpose vs environment-specific)
- [x] Wire snapshots into the agent loop (Phase 5 owns the loop)

## Verification

- `uvx ruff check .` — clean
- `pytest` — 268 passed, 3 skipped
- Live sim: `count=6`, versions 1..6, pose present from the second snapshot on
