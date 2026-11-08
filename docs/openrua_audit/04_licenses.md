# 04 — Licences and Reuse Rules

Two questions this document answers: *may* we reuse upstream code, and *should*
we, given what it drags along.

## 1. Repository licences

| Repository | Licence | Evidence |
|---|---|---|
| Target `terrense/ros2-multimodal-robot-collab` | MIT, © 2026 terrense | `LICENSE:1-3` |
| Upstream `Open-RUA/uni-robot-agent` | MIT, © 2026 uni-robot-agent contributors | `LICENSE:1-3`, `pyproject.toml:7-8` |

MIT → MIT is compatible. If code is copied verbatim, the MIT notice must be
preserved and the origin recorded. This is permitted by licence and still
restricted by the master plan: **Phase 0 copies nothing**, and no later phase
may copy without a provenance record (see §5).

## 2. What upstream's licence does not cover

Upstream is MIT, but it is not dependency-free. `pyproject.toml:21-33` pins:

```
openai-codex==0.147.0
claude-agent-sdk==0.2.165
```

and ships a service stack (`fastapi`, `uvicorn`, `httpx`), a TUI (`textual`),
model-hub access (`huggingface-hub`) and schema tooling (`pydantic`,
`jsonschema`).

Consequences:

- Reusing `provider/` or `agents/` means adopting vendor SDKs with their own
  terms and their own release cadence, in a repository whose runtime
  dependencies today are numpy and PyYAML. That is a large, mostly unnecessary
  expansion for a ROS 2 project.
- `pydantic` and `jsonschema` are the only two upstream runtime dependencies
  our contract work would actually want. Both are MIT/BSD and small.

This is a licence-adjacent reason to prefer *reimplementing the mechanism*
over importing the module: the mechanism is a page of logic; the module is a
dependency tree.

## 3. Vendored third-party obligations in the target

Already recorded in `THIRD_PARTY.md` (root of the target repository); repeated
here because a migration may tempt someone to redistribute a component:

| Component | Licence | Binding consequence |
|---|---|---|
| `third_party/ultralytics` | AGPL-3.0 (or commercial) | network-service use triggers copyleft; a container image that runs it and is distributed carries obligations |
| `third_party/VINS-Mono` | GPLv3 (`third_party/VINS-Mono/README.md`) | distribution of a binary that links it requires source |
| `third_party/openpose` (+ caffe fork, pybind11) | CMU academic / non-commercial | **non-commercial research only** |

Because `third_party/openpose` is non-commercial, this repository as a whole
cannot be positioned as a commercially redistributable product without
removing or replacing that component. Any Phase 0 design that would make the
repository easier to ship as a service must not quietly increase dependence on
OpenPose.

Note on evidence quality: `third_party/VINS-Mono` has **no `LICENSE` file at its
root**. The GPLv3 statement comes from `third_party/VINS-Mono/README.md` and
matches `THIRD_PARTY.md`. Upstream snapshot SHAs for every vendored component
are pinned in `THIRD_PARTY.md`.

## 4. Generated assets

No model weights are tracked in either repository at the audited revisions;
`models/` contains only `README.md` and a `.gitignore` (target). Nothing in this
audit depends on asset licensing. Model weights downloaded later keep their own
terms and are out of scope for Phase 0.

## 5. Reuse rules for later phases

1. **Reference, don't vendor.** Upstream stays a read-only clone outside the
   tree. No submodule, no copy of directories.
2. **Reimplement mechanisms, copy functions only when small and isolated.**
   A file under ~100 lines that encodes a contract (for example the field
   semantics of `tools/results.py`) is a candidate for adaptation; a subsystem
   with transitive dependencies is not.
3. **Provenance record.** Every adapted file carries, at the top:

   ```
   Origin: Open-RUA/uni-robot-agent @ b972237dcf1baa0c0a5cdf34ba062fcfba619f83
   File: <path in upstream>
   Licence: MIT (see THIRD_PARTY.md)
   Changes: <what was changed>
   ```

   and a row is added to `THIRD_PARTY.md`.
4. **No new dependency without a licence check.** In particular, do not add
   `openai-codex`, `claude-agent-sdk`, `textual` or `fastapi` to this project's
   runtime dependencies as a side effect of "adopting the harness".
5. **Attribution survives refactoring.** If an adapted function is later
   rewritten beyond recognition, remove the attribution only when the
   provenance is genuinely gone; otherwise keep it.

## 6. Open questions (to settle before Phase 1 merges)

- Does the project intend any commercial distribution? The OpenPose licence says
  no, today. This constrains how the image and the agent runtime may be
  published.
- If `pydantic` is adopted for contracts, does it belong in runtime
  dependencies (the ROS 2 image) or only in the test/dev path? The ROS 2
  packages should probably keep validating with stdlib + `jsonschema` and leave
  pydantic to the harness side.
