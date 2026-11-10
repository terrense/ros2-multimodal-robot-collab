# Phase 7 Tasks

> **OpenRUA assimilation — Phase O7.** Not to be confused with the post-training
> line (`PT7` in `EXPERIMENT_LOG.md`) or the robot roadmap (`R7` in
> [`docs/roadmap.md`](../../roadmap.md). See [`docs/specs/README.md`](../README.md).

- [x] User approves the spec and the four recommendations
- [x] `training/provenance.py`: hashes (prompt, schema, benchmark), v2 record helpers
- [x] `training/envs/multi_turn_env.py`: emit provenance + receipt on v2 records (additive)
- [x] Dataset builders: refuse drifted hashes, identical output for v1 inputs
- [x] `training/run_record.py`: write/validate `runs/<kind>/<name>.json`
- [x] `training/report.py`: generated markers, `unverified` cells, audit block
- [x] `docs/results.md`: switch to markers without changing measured values
- [x] `training/data/build_dpo.py`: verified clone hash guard for counterfactuals
- [x] Tests P7-R1..R9
- [x] README status block updated on merge
- [x] Full suite green (294+)
- [x] Report before Phase 8
