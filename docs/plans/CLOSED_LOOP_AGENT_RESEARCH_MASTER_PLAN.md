# Master Task: A Closed Loop with Real Physics, and What Transfers Across Bodies

## Mission

Turn the existing ROS 2 agent stack into a **closed loop over physical state**:
a task whose failure is a real failure, an agent that observes it and replans
inside a budget, a recovery that is *verified* before it is remembered, and a
memory that says **when** it applies to a different robot.

The measurable claim is not "an LLM calls ROS 2 actions". It is:

> A robot's failure experience can be turned into semantic skill knowledge with
> explicit applicability conditions, and that knowledge can be transferred
> reliably across embodiments and dynamic human-robot workspaces.

## Immutable rules

1. **No result without an executed run.** A measurement that was not produced
   by the code in this repository is marked `NOT RUN`, never `PASS`. The
   published table (`docs/results.md`) is generated from committed artefacts;
   if the code changes, the artefacts and the table are regenerated.
2. **Three levels of evidence stay apart.** A *tool call* succeeding, a
   *physical action* succeeding, and a *task goal* being verified are three
   different things. The repository already distinguishes them; the plan must
   not blur them.
3. **`UNKNOWN` is not `FAILED`.** An unconfirmed effect stays unknown and is
   not replayed. This is the runtime's existing rule and it is not relaxed.
4. **The LLM never issues joint commands.** It chooses what to do, on which
   robot, and what to do about a failure. Motion comes from MoveIt2 /
   ros2_control / the vendor SDK, and physical safety stays outside the LLM.
5. **The simulator's ground truth is for training labels and evaluation only.**
   It is never visible to a deployed policy.
6. **Counterfactuals only on a verified restore.** `training/counterfactual.py`
   already refuses a clone whose state hash does not match. The same rule
   applies to the physical stack: no candidate recovery is scored on a state
   that was not restored and verified.
7. **Keep the frozen benchmark.** The 400-episode
   `benchmarks/fault_injection_v1.jsonl` and its split stay untouched.
8. **Do not delete existing behaviour to make room for new behaviour.** The
   harness, the ledger, the calibration and the CI are the measurement
   apparatus; they are the last thing to change.

## Baseline (verified 2026-11-25, against this repository)

What exists and works:

| Piece | Where | State |
|---|---|---|
| Agent runtime, contracts, receipts, durable ledger | `agent_harness/runtime/`, `agent_harness/contracts/` | works, verified against a live ROS 2 graph |
| L1 environment, 13 actions, 9 fault families | `agent_env/l1_sim.py`, `agent_env/faults.py` | works, 558 tests |
| L2 live-stack interface behind the same shape | `agent_env/l2_gazebo.py` | works; it says plainly what it cannot report (`holding_tool`, `operator_verified` are `unknown`) |
| Verified counterfactual clones | `training/counterfactual.py` | works at L1; **refuses at L2** ("no verified restore") |
| Frozen benchmark, expert policy, rules baseline | `benchmarks/`, `agent_env/expert_policy.py` | 400 episodes; expert 100%, rules 76.1% |
| Calibration against the live stack | `agent_env/calibration_runner.py` | 9/9 families measured, 4 falsify L1 |
| Live robot stack | `src/robot_collab_*` | base drives, camera + depth cloud + detector work, a mission runs end to end |
| gz-sim port | `docs/gz_sim_port.md` | the lab **and** the workshop run; the base drives; a mission runs |
| Arm execution | `src/robot_collab_manipulation/.../arm_skill_server.py` | **simulated**: `simulate_step_seconds`, then success. It can be armed with `miss_once` / `drop` faults. |

What is missing, and why it matters:

| Gap | Evidence | Consequence |
|---|---|---|
| The agent does not observe | `agent_harness/agent/policies.py`: `PlanMacroPolicy.decide()` starts with `del view` | the loop is open: the plan is replayed, a failure ends it |
| The arm does not move anything | `arm_skill_server.py` sleeps and reports success | no physical result, nothing to verify, no real failure |
| No restore on the physical stack | `training/counterfactual.py` allows counterfactuals only at L1 | a recovery cannot be *verified* before it is remembered |
| No embodiment layer | `agent_harness/contracts/` checks that a capability exists, not that *this* robot can do it now | no capability-aware planning |
| No trained policy | `docs/results.md`: 6 of 7 arms pending | the agent is the hand-written rule table |

## The three claimed contributions

Stated as hypotheses to be tested, not as results:

1. **(main) Counterfactually verified recovery memory, on a physical stack.**
   A candidate recovery is scored by replaying it from a restored physical
   state and comparing verified task progress. Only a recovery with a
   measurable gain enters the memory, and each entry carries the conditions
   under which it applies.
   *Novelty caveat*: ReSYNC (CoRL 2026) learns from failures and MAGMA-GEN
   (CoRL 2026) verifies corrections by re-execution. Both work in their own
   simulators. The claim here is narrower and checkable: the verification runs
   on a **physical** ROS 2 stack, and the memory is split into
   robot-invariant and robot-conditional knowledge, and its transfer is
   measured with a leave-one-robot-out split.
2. **Capability-conditioned feasibility.** Four gates before a skill may run:
   semantic preconditions, kinematic reachability, collision freedom, runtime
   state - with `true / false / unknown`. A static profile is only a coarse
   filter; the runtime check is the contribution.
3. **Verifier-guided post-training.** The teacher is the hosted API already
   wired in `agent_harness/agent/hosted_model.py`; the student is a small
   local policy. The training signal is the physical verifier's progress, not
   a text simulator's.

Not claimed as contributions, only as experimental vehicles: the simulated
human, the mobile base's transport task, the grasping.

## The five gaps between the current loop and a closed loop

1. **A physical verifier.** "The tool is in the gripper" and "the tool is on
   the shelf" must be decided by the simulator's state, not by a sleep.
2. **Observation into the decision.** `decide(view)` must read the view, and
   a failure must produce *candidates*, not `Finish`.
3. **Budgeted replanning.** Observe -> decide -> verify -> replan, inside a
   budget, instead of replaying a macro.
4. **Verified recovery memory.** Candidate -> restore -> replay -> verify ->
   remember.
5. **Episode-level records.** Seed, environment version, action, receipt,
   verifier verdict, recovery cost - the raw material of every table.

## Phases, in dependency order

Each phase has an acceptance gate that is an **executed observation**, not a
claim.

### R5.1 A gripper that holds (the spike; everything else depends on it)

The Dobot on the chassis is a prop: all six joints in
`src/robot_collab_sim/urdf/dobot_mounted.urdf` are `type="fixed"`. The
official description is present and complete -
`third_party/robot_descriptions/dobot_6axis/cra_description/urdf/cr10_robot.xacro`
has six revolute joints with axes and limits, and
`cr10_moveit/config/cr10_robot.ros2_control.xacro` has the ros2_control
block.

Do, in gz-sim first (its engine handles articulated robots; the ODE solver
in Gazebo Classic produced NaN velocities on these free joints, which is why
they were frozen):

- unfreeze the six joints on the chassis's arm, keeping the mount fixed;
- add a two-finger gripper (the official description has none);
- drive it with gz-sim's joint controller system, bridged from ROS the way
  `robot_collab_gz_bridge` bridges the twist;
- pick and place the tool box (0.12 x 0.08 x 0.06 m at 1.25, -0.75, 0.41).

**Gate**: the tool's pose changes with the gripper and the release leaves it
where it was put, as observed in the simulator's model states. If a parallel
jaw cannot hold it in this engine, fall back to a suction-style end
effector and say so in the limitations - do not fake the grasp.

### R5.2 A verified restore on the physical stack

gz-sim has `set_pose_vector` (`ignition.msgs.Pose_V` -> `Boolean`) on
`/world/<world>/set_pose_vector`: the poses of every model can be set in one
call. There is no world save/restore in Fortress (the `state` service is the
logging service, `SerializedStepMap`).

Do: snapshot the poses of the base, the arm and the objects; restore them;
hash the observable state (the same idea as `training/counterfactual.py`'s
`state_hash`) before and after; refuse a restore whose hash does not match.

**Gate**: a restored clone hashes equal to the snapshot, and
`training/counterfactual.py`'s rule ("no verified restore, no counterfactual")
extends from L1 to the physical stack.

### R5.3 The loop closes

`PlanMacroPolicy` becomes an observe-decide-verify-replan loop: the view
carries the last receipt and the verifier's verdict; a failure yields
candidates within the budget; the episode records every step.

**Gate**: a deliberately injected failure makes the agent choose a different
action rather than terminate, and the episode log shows it.

### R5.4 The workcell: humans, objects, stations

A scripted human (state machine, not an agent: `IDLE -> WALKING ->
REQUESTING -> WAITING -> CONTRIBUTING -> LEAVING`) with four measurable
perturbations (interruption, contribution, obstruction, correction); objects
with mass, collision and a graspable state; stations; a seeded, resettable
task generator.

**Gate**: every scenario reproduces from its seed; the human changes the task
state, not just the picture.

### R5.5 The embodiment layer

Robot profiles (reach, payload, tool, controller), the four feasibility
gates, and a scene graph whose facts carry `true / false / unknown`, a
timestamp and a source.

**Gate**: the same task runs on two different arms without touching the
planner, and a clearly unreachable target is refused with a reason.

### R5.6 The benchmark and the ablations

Leave-one-robot-out; the six arms (rules, prompt-only LLM, +ontology,
+feasibility, +verified memory, +post-training); the metrics that separate
success from safe refusal: task success, recovery success, safe termination,
**false feasibility rate**, transfer gap, recovery cost, memory precision.

**Gate**: every group runs on the same task instances, with the seeds and
the artefacts committed.

### PT5 Post-training on the new environment

The hosted teacher generates trajectories and recovery episodes; the local
student is trained on the verifier's progress. **The machine has an NVIDIA
GTX 1650 with 4 GB** (`nvidia-smi` sees it from WSL) and **no torch is
installed** - a 30B teacher is out of the question, a 1.5-3B policy with
LoRA is not. The hosted API is the teacher; this is both cheaper and more
reproducible.

### R7 Real hardware (only when the devices are available)

The official `aubo_ros2_driver` and `aubo_description` exist, with the URDFs
and the calibration tooling. The order is: the URDF, TF, joint states and
MoveIt2 in an isolated workspace -> `use_fake_hardware` -> calibration and
TCP -> guarded real motion -> then the agent. A result from real hardware is
labelled as such; a simulation result is never presented as one.

## Risks, and what to do about them

| Risk | Why it is real | Mitigation |
|---|---|---|
| The gripper does not hold in the simulator | no mimic-joint support; a small box on a two-finger jaw | the spike is R5.1, first; a suction fallback is declared in the limitations |
| The transfer effect is small | recovery knowledge may not cross bodies | use a body pair with a large capability gap (i5H 886.5 mm vs i10H 1350 mm reach); the "memory without verification" arm is mandatory |
| The probe is flaky | `visual_occlusion` depends on the base's exact pose and path; it reproduces on a warm stack, not always on a cold one | seed the scenes, warm the stack before a measurement, and report the seeds |
| The numbers do not reproduce | the L1 fault distribution and the live stack's differ; four of nine families already falsify L1 | keep calibrating against the live stack, never against L1 alone |
| Scope creep into a platform | the conversation's plan listed five new top-level directories | the phases above deliver working code, not empty directories |

## What not to do yet

- Do not build a large ontology. A Python dataclass graph plus explicit rules
  covers the four gates; RDF/OWL/SHACL only if the rules need it.
- Do not start training before R5.3 produces physical execution evidence: a
  policy trained on a simulator that reports success it did not achieve
  learns the simulator's fiction.
- Do not migrate the whole stack to Gazebo Harmonic. The gz-sim port is
  running; two simulators are an asset for the paper, not a debt.
- Do not let the LLM touch joint commands.

## The immediate next action

R5.1, as a spike: unfreeze the arm's six joints in gz-sim, add a gripper,
and try to hold the tool box.

```bash
# the world with the arms and the robot
bash scripts/run_gz_sim_demo.sh --workshop --gui

# the same check the lab scene has
bash scripts/check_gz_sim.sh
```

**The answer decides the route**: if the jaw holds, R5.1 continues into R5.2;
if it does not, the end effector changes and the limitation is written down
before anything else is built on top of it.
