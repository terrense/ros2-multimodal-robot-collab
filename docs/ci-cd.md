# CI/CD

Two workflows, one contract: **a branch is mergeable only when its checks are
green, and every commit on `main` produces an image anyone can run.**

```
push / PR
   │
   ├─ changes ──────────── decides if src/ was touched ──┐
   ├─ lint            ~20s   ruff + conflict markers     │
   ├─ python-tests    ~40s   163 tests + benchmark + acceptance
   ├─ harness-tests   ~25s   agent_harness suite          │
   │                                                      ▼
   └─ ros2-build      ~5min   10 colcon packages (only if src/ changed)

push to main / tag v*
   └─ container       ~6min   build → push ghcr.io → smoke test
```

## Jobs

| Job | Trigger | What it proves | Where it lives |
|---|---|---|---|
| `changes` | always | — (routing only) | `ci.yml` |
| `lint` | always | code parses, imports resolve, no stale merge markers | `ci.yml` |
| `python-tests` | always | `agent_env` + `training` still behave | `ci.yml` |
| `harness-tests` | always | the plan/skill layer still behaves | `ci.yml` |
| `ros2-build` | `src/`, `scripts/`, Dockerfile changed | all ten packages compile and their tests pass under Humble | `ci.yml` |
| `container` | push to `main`, tag `v*` | the workspace is deployable | `docker.yml` |

## Why it is shaped this way

**Cheap checks gate expensive ones.** `lint` fails in twenty seconds; a broken
`src/` build takes five minutes to tell you the same thing. The router job
means a README edit never pays for a colcon build.

**Do not hand-roll what upstream maintains.** The old `ros2-build` job ran
`apt install ros-humble-ros-base` without ever adding `packages.ros.org`. It
failed on every run since it was written, so the ten ROS2 packages had no
build check at all. Running inside `ros:humble-ros-base` makes the toolchain
someone else's maintenance problem.

**Install only what the tests import.** `torch` used to be installed on every
run — a 2GB download — while every training module imports it lazily inside a
function. The suite runs on numpy alone. Dependency hygiene is CI speed.

**Gates start narrow.** `ruff.toml` selects `E4,E7,E9,F,I,W,B`: real defects,
all auto-fixable, 39 findings on day one instead of 900. Widen it deliberately,
never by adding `# noqa`.

**A green build is not a working image.** The container job runs the pushed
image, sources the overlay and lists packages. Build success and runtime
success are different claims.

## Reading a failed run

1. Open the **job**, not the run. The run summary only aggregates.
2. Click the **red step**; the first error line is usually the real one.
3. `lint` failures: run `ruff check . --fix` locally, commit, push.
4. `python-tests` failures: reproduce with the pinned environment —
   `uv venv .venv --python 3.11 && uv pip install -r requirements-dev.txt`
   then `.venv\Scripts\python -m pytest`.
5. `ros2-build` failures: reproduce in the same image —
   `docker run --rm -v "%CD%:/ws" -w /ws ros:humble-ros-base bash`.
6. `container` failures: run `docker build .` locally; it uses the same
   Dockerfile and cache layout.

## Local pre-flight

```bash
uv pip install pre-commit        # or: pip install -r requirements-dev.txt
pre-commit install               # once per clone
```

From then on, every `git commit` runs ruff plus the hygiene hooks against the
staged files. CI runs the same ruff check, so the gate should almost never be
the first place you learn about a problem.

## Releasing and using the image

```bash
git tag v0.2.0 && git push origin v0.2.0
```

Produces `ghcr.io/terrense/ros2-multimodal-robot-collab:0.2.0` (and `0.2`).
Every push to `main` also publishes `:edge`.

```bash
docker pull ghcr.io/terrense/ros2-multimodal-robot-collab:0.2.0
docker run --rm -it ghcr.io/terrense/ros2-multimodal-robot-collab:0.2.0 \
  ros2 launch robot_collab_bringup demo.launch.py
```

The image contains `src/` and the compiled overlay. It does **not** contain
`third_party/` (203MB of vendored Ultralytics, VINS-Mono and OpenPose
snapshots) — perception backends are mounted or built separately, and the
nodes report a clear error when they are missing.

## Known limits

- **No `colcon test` coverage yet.** The job runs it, but the packages do not
  define ROS-side tests; it passes vacuously. Adding `ament_cmake_pytest` /
  `launch_testing` tests to the packages is what turns this job from "it
  compiles" into "it works".
- **No ARM image.** The robot's Jetson TX2 is `linux/arm64`; the published
  image is amd64. A `platforms: linux/amd64,linux/arm64` build (QEMU) is the
  next step if the Jetson should run the same artifact.
- **No model or data artifacts.** Checkpoints and datasets stay out of the
  image by design; they belong in a registry with their own versioning.
