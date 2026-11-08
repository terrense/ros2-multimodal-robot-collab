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

## Three failures this pipeline found on day one

Worth reading before you touch the Dockerfile or the ROS job. All three were
invisible while the old pipeline was red or absent.

1. **`ament_python` is not a rosdep key.** All nine Python packages declare it
   as a build tool (the ROS 2 template does), and rosdep has a rule for
   `ament_cmake` but none for `ament_python` — it is a build type, not a
   dependency. `rosdep install` aborts on every Python package.
   Fix: `--skip-keys ament_python`, explicitly. Do not reach for `-r`, which
   would swallow genuinely unknown dependencies too.

2. **rosdep needs the apt index you just deleted.** A layer that ends with
   `rm -rf /var/lib/apt/lists/*` leaves nothing for rosdep's `apt-get install`
   to read, and the error is `Unable to locate package ros-humble-cv-bridge`.
   Fix: `apt-get update` inside the rosdep layer, cleanup after it.

3. **`~/.bashrc` is not read by `bash -lc`.** Writing the overlay there made
   `docker run IMAGE ros2 pkg list` come back empty while the smoke test
   passed, because the test sourced the overlay by hand. A test that does not
   use the product the way users do is not testing the product. Fix: an
   entrypoint that sources base + overlay, and a smoke test that runs the
   natural command.

## Day-to-day

```bash
git switch -c feat/whatever
# ... work ...
git commit            # pre-commit runs ruff on the staged files
git push -u origin feat/whatever
# open a PR -- CI runs on it, and the PR cannot merge red
```

Turn the gate into a rule, not a habit: **Settings > Branches > Add branch
protection rule** for `main`, require the status checks `lint`, `python-tests`,
`harness-tests`, and require a branch to be up to date before merging. Without
that, "the checks are green" is a convention one tired evening can break.

## Releasing and using the image

```bash
git tag v0.2.0 && git push origin v0.2.0
```

Produces `ghcr.io/terrense/ros2-multimodal-robot-collab:0.2.0` (and `0.2`).
Every push to `main` also publishes `:edge`.

```bash
docker pull ghcr.io/terrense/ros2-multimodal-robot-collab:0.2.0

# The entrypoint sources the overlay, so this just works:
docker run --rm ghcr.io/terrense/ros2-multimodal-robot-collab:0.2.0 ros2 pkg list

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
- **The image is 4.7GB.** The runtime stage inherits the full `deps` stage,
  which carries the ROS base plus every resolved dependency but no build
  toolchain. Trimming means starting from `ros:humble-ros-core` and installing
  only the packages the launch files actually need.
- **No ARM image.** The robot's Jetson TX2 is `linux/arm64`; the published
  image is amd64. A `platforms: linux/amd64,linux/arm64` build (QEMU) is the
  next step if the Jetson should run the same artifact.
- **No model or data artifacts.** Checkpoints and datasets stay out of the
  image by design; they belong in a registry with their own versioning.
