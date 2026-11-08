# 00 — Baseline

Phase 0, step T1. Everything below is either measured on this machine or quoted
from a workflow run; nothing here is inferred.

## Pinned revisions

| Role | Repository | Commit | Branch at capture | Worktree |
|---|---|---|---|---|
| Target | `terrense/ros2-multimodal-robot-collab` | `e2009d02ff24ca87bda653e1d2fb66a12541d793` | `phase0/two-repo-audit` | clean |
| Upstream | `Open-RUA/uni-robot-agent` | `b972237dcf1baa0c0a5cdf34ba062fcfba619f83` | default (`main`) | clean |

Upstream HEAD commit metadata:

```
b972237dcf1baa0c0a5cdf34ba062fcfba619f83
2026-11-08 18:44:22 +0800
Merge pull request #22 from yuxinzuo/feat/antigravity-backend
```

Reproduction:

```bash
git clone --depth 1 https://github.com/Open-RUA/uni-robot-agent.git D:/study_claudecode/_audit/uni-robot-agent
git -C D:/study_claudecode/_audit/uni-robot-agent rev-parse HEAD   # must match above
```

The upstream clone lives **outside** the target repository. It is reference
material: not a submodule, not vendored, not a build input.

Note: upstream is a fast-moving repository (HEAD is a merge from the same day
this audit started). Every finding below is only valid for the pinned SHA.

## Target inventory

| Item | Count | Command |
|---|---|---|
| Tracked files | 2987 | `git ls-files \| wc -l` |
| ROS 2 packages | 10 | directories under `src/` containing `package.xml` |
| Launch files | 4 | `src/**/*.launch.py` |
| Pytest files (top-level suite) | 15 | `tests/test_*.py` |
| Harness test files | 1 | `agent_harness/tests/test_*.py` |
| Python test functions | 182 run (163 + 19) | see baseline checks |

Package and subsystem map:

```
src/robot_collab_{agent,bringup,core,hri,interfaces,manipulation,
                  navigation,perception,sim,slam}/     ROS 2 Humble packages
agent_harness/scripts/                                  plan -> ROS 2 bridge
agent_env/                                              L1 step environment + protocol
training/                                               SFT / DPO / PPO / OPD + evaluation
skills/*/schema.json                                    executable skill contracts
docs/                                                   architecture, results, plans
third_party/                                            vendored upstream snapshots (excluded)
```

Data assets that must not move in this phase: the frozen 400-episode benchmark
under `benchmarks/`, the data splits consumed by `training/data/`, and the
recorded results in `docs/results.md`.

## Upstream inventory

| Item | Count | Command |
|---|---|---|
| Tracked Python files | 304 | `find . -name '*.py' -not -path './.git/*'` |
| Python lines | 33100 | same set, `cat \| wc -l` |
| Repository size | 7.3 MB | `du -sh` |
| Test files | **0** | see below |

Module sizes (files / lines):

| Package | Files | Lines |
|---|---|---|
| `integrations/` | 74 | 6348 |
| `services/` | 56 | 5691 |
| `agents/` | 34 | 4453 |
| `provider/` | 40 | 4232 |
| `tools/` | 33 | 3684 |
| `ui/` | 14 | 1948 |
| `context/` | 8 | 1151 |
| `execution/` | 8 | 1117 |
| `artifacts/` | 8 | 706 |
| `observation/` | 3 | 575 |
| `events/` | 3 | 455 |
| `telemetry/` | 6 | 453 |
| `resources/` | 1 | 1 |
| top level (`cli`, `config`, `session`, `__main__`) | 5 | 1487 |

### Upstream has no tests

Searches run at the pinned SHA, all returning nothing:

```bash
find . -name 'test_*.py' -o -name '*_test.py'        # 0 results
find . -type d -name 'tests'                          # 0 results
grep -n "pytest" pyproject.toml                       # no test configuration
```

**Confirmed fact.** This matters for adoption: upstream mechanisms cannot be
trusted because "their tests pass". Any component we adapt inherits our test
obligation, not theirs. It also means upstream has no executable specification
of its own contracts — the docstrings and type hints are the specification.

## Licences

| Component | Licence | Obligation relevant to us |
|---|---|---|
| Target repository | MIT (`LICENSE:1`) | permissive |
| Upstream repository | MIT (`LICENSE:1`) | permissive; attribution required when code is copied |
| `third_party/ultralytics` | AGPL-3.0 | network-copyleft if distributed as a service |
| `third_party/openpose` | CMU academic / non-profit non-commercial | **no commercial use** |
| `third_party/VINS-Mono` | GPLv3 (stated in `third_party/VINS-Mono/README.md:1`; no `LICENSE` file at its root) | copyleft on distribution |

Consequences for this project, recorded here so later phases cannot drift:

1. MIT → MIT reuse of upstream code is permitted **with attribution**. This
   phase still copies nothing; Phase 0 is read-only.
2. `third_party/openpose` already restricts the repository to non-commercial
   research use. Nothing we add may imply commercial redistribution.
3. Vendored third-party source is excluded from line-by-line business-logic
   audit per the master plan; it is listed here only for licence accounting.

## Baseline checks

Run on this machine (Windows 11, Python 3.11.15 in `.venv`, ruff 0.17.4):

| Check | Command | Result |
|---|---|---|
| Lint | `uvx ruff check .` | `All checks passed!` |
| Python suite | `.venv\Scripts\python -m pytest` | `163 passed, 3 skipped in 2.66s` |
| Harness suite | `.venv\Scripts\python -m pytest agent_harness/tests` | `19 passed in 0.08s` |

ROS 2 build: **NOT RUN locally** — this host has no ROS 2 installation.
Equivalent evidence from CI on the same source tree:

- CI run `42478137319` at commit `7d9dbf3` — **success**; its `ros2-build` job
  runs `colcon build --base-paths src` inside `ros:humble-ros-base`.
- CI run `42476683816` at commit `b0a084e` — `ros2-build` **success** in 126 s
  (job duration measured from the Actions API).

Hardware, Gazebo and live-robot behaviour: **NOT RUN**. No claim in this audit
depends on them.

## Scope of this audit

- Target: all non-vendored Python; ROS 2 packages read at the interface level
  (action/service contracts, state machine), not line-by-line for every node.
- Upstream: the harness spine (`cli`, `config`, `session`, `execution`,
  `tools`, `integrations/base`, `artifacts`) read line-by-line; peripheral
  packages (`provider`, `context`, `agents`, `services`, `integrations/*`,
  `ui`) read for contract shape, with symbols cited in `01_upstream_callgraph.md`.
- Excluded: `third_party/`, generated `build/ install/ log/`, binary assets.
