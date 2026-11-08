# 02 — Target Callgraph

Repository: `terrense/ros2-multimodal-robot-collab` @ `e2009d02`.
Every claim carries `path:line`. Claims marked **UNVERIFIED** were listed from
symbols but not read line-by-line in this pass.

## 1. Two execution paths that never meet

The repository contains two independent agent execution paths:

```
A. Mission plan (one-shot)          B. Step loop (training / L1)
   minimax_mission_planner             policy (LLM / rule / expert)
        -> SkillRegistry                   -> MultiTurnDeliveryEnv.step
        -> RosToolExecutor                 -> L1DeliveryEnv.step
        -> ROS2 Action/Service/Topic       -> reward components
        -> MissionStateMachine             -> EpisodeRecord
```

They share neither a plan schema nor a step contract. The divergence is stated
by the code itself in `agent_env/protocol.py:3-6`: "Unlike the open-loop mission
plans in agent_harness (one blind JSON plan per mission), training uses a
step-level loop".

## 2. Path A — mission plan execution

### 2.1 Plan generation

`minimax_mission_planner.py`:

| Step | Line | Behaviour |
|---|---|---|
| Build request | `minimax_mission_planner.py:176-184` | one HTTP call to MiniMax, `response_format` optional |
| Parse | `:126-137` | strips code fences, falls back to first `{...}` regex |
| Validate | `:187-188` | `SkillRegistry.load` + `validate_plan` |
| Optional dry run | `:191-199` | renders the command that *would* be sent |
| Execute | not here | execution is a separate CLI (`ros_tool_executor.py`) |

There is no loop: the plan is produced once, validated once, then handed to the
executor. No observation is fed back, and no step is revised.

### 2.2 Validation

`ros_tool_executor.py`:

- `SkillRegistry.load` (`:65-74`) reads `skills/*/schema.json` and refuses to
  start if the directory yields no skills.
- `validate_plan` (`:87-122`) enforces: required top-level fields
  (`intent`, `operator_id`, `steps`) at `:27`, non-empty step list `:91-92`,
  per-step `skill`/`action` `:98-102`, required args `:108-113`, and a command
  whitelist `:115-119`.
- `get_action` (`:76-85`) also rejects an action marked `callable: false`.
- `normalize_args` (`:130-151`) injects defaults: operator id, confirmation
  requirement from `plan["safety"]`, source tags.

Validation is schema-level only. It does not check that the declared station
exists, that two steps do not conflict, or that resources are free.

### 2.3 Dispatch

`SkillRegistry` action schema declares the transport; `transport_for`
(`:154-161`) maps it to `action` / `service` / `topic`.

`RosToolExecutor.execute_plan` (`:281-323`):

- Sequential loop over validated calls (`:289`); one call at a time.
- On any call whose status becomes `failed`, the loop `break`s
  (`:321-322`) — later steps are dropped, not cancelled or compensated.
- Action path `_execute_action` (`:333-368`): `wait_for_server` (`:336`),
  `send_goal_async` (`:346`), spin until accepted (`:347`), spin until result
  (`:355-359`), status 4 → `succeeded` (`:363`).
- Service path `_execute_service` (`:370-382`): one call, `succeeded` on any
  response.
- Topic path `_execute_topic` (`:384-394`): publishes three times and returns
  `succeeded` unconditionally.

### 2.4 Robot side

`src/robot_collab_core/robot_collab_core/mission_state_machine.py`:

- `MissionStateMachine(Node)` (`:29`) owns the mission.
- Publishes `/mission/events` (`:46`).
- Hosts the `DeliverTool` action server (`:61`), accepts goals in
  `_handle_goal` (`:74`), handles cancel in `_handle_cancel` (`:83`).
- Exposes `QuerySystemState` service (`:53`).
- Builds results in `_result` (`:287`).

**UNVERIFIED in this pass**: the state transitions and the exact success
condition inside the state machine; being confirmed by the subsystem audit.

## 3. Path B — step-level training loop

### 3.1 Contracts

`agent_env/protocol.py`:

- `Action` (`:77-100`): `skill`, `action`, `args`, `reason`; name is
  `skill.action` (`:86-88`).
- `Observation` (`:126-150`): task, location, holding, operator verification,
  detections, last action/result, safety events, no-progress count, steps
  remaining.
- `StepResult` (`:103-113`): `status` in `succeeded|failed|rejected|none`,
  `error_code`, `message`, `data`.
- `Outcome` (`:66-74`): `success`, `correct_abort`, `cancelled_ok`,
  `wrong_termination`, `step_limit`, `running`.
- `ActionRegistry` (`:153-189`): loads `agent_env/schemas/agent_skills.json`,
  validates action legality, and renders the system prompt catalogue
  (`:191-209`).

### 3.2 Step loop

`training/envs/multi_turn_env.py`:

- `MultiTurnDeliveryEnv.step(assistant_text)` (`:177-215`):
  parse text → `Action` (`:183`), registry validation (`:185`),
  `env.step(action)` (`:188`), build `TurnRecord` (`:193-206`),
  append observation/messages (`:208-213`).
- `TurnRecord` (`:52-68`) keeps observation, raw text, parse status, action,
  result, reward components, info, `invalid`, `malformed`.
- `EpisodeRecord` (`:71-135`) is the trajectory container; `outcome` reads
  `summary["outcome"]` (`:81-83`).

`agent_env/l1_sim.py`:

- `L1DeliveryEnv.step` (`:210-267`): validate → `_apply` dispatch (`:224`),
  no-progress tracking (`:233-238`), step limit → `STEP_LIMIT` (`:241-242`),
  reward components (`:248-254`), info including `outcome`, `progress`,
  violation counters (`:258-266`).
- Reward components are: `progress_delta`, `grounding_violation`,
  `safety_violation`, `step_cost`, `terminal_success` (`:248-254`).
- `_apply` (`:278-315`) dispatches per action; each `_act_*` returns
  `(StepResult, grounding_flag, safety_flag)`.

## 4. State ownership

| State | Owner | Evidence |
|---|---|---|
| Mission phase / delivery progress | `MissionStateMachine` node (path A) | `mission_state_machine.py:29,61` |
| Robot pose, joints, detections | ROS 2 topics (no single owner) | nodes publish; no authoritative store |
| Episode state, step index, outcome | `L1DeliveryEnv` (path B) | `l1_sim.py:210-267` |
| Chat history | `MultiTurnDeliveryEnv` | `multi_turn_env.py:171-174,208-213` |
| Evaluation outcome | `EpisodeRecord.summary["outcome"]` | `multi_turn_env.py:81-83` |
| Plan validation | `SkillRegistry` (path A) | `ros_tool_executor.py:60-122` |
| Action legality | `ActionRegistry` (path B) | `protocol.py:153-189` |

Two validators, two schemas, two vocabularies — see §5.

## 5. The contract split, measured

Action vocabularies (re-measured during Phase 1; the first pass counted
declared actions without checking `callable`):

| Source | Actions |
|---|---|
| `skills/*/schema.json` (path A) | 8 declared, **6 agent-callable**: `hri.publish_tts`, `manipulation.publish_arm_control`, `mission-control.deliver_tool`, `navigation.navigate_to_station`, `perception.verify_operator`, `system-state.query_state`. Declared but `callable: false`: `perception.recognize_openpose_gesture`, `perception.detect_tool_yolov8` — perception internals the model cannot invoke. |
| `agent_env/schemas/agent_skills.json` (path B) | 13: `hri.{publish_tts,request_confirmation}`, `manipulation.{handover_tool,pick_tool,publish_arm_control,select_grasp_pose,verify_grasp}`, `mission.terminate`, `navigation.{navigate_to_station,reposition}`, `perception.{request_tool_detection,verify_operator}`, `system-state.query_state` |

**5 names** appear in both callable sets: `hri.publish_tts`,
`manipulation.publish_arm_control`, `navigation.navigate_to_station`,
`perception.verify_operator`, `system-state.query_state`. The union is 14
capabilities, of which exactly one (`mission-control.deliver_tool`) exists only
on the ROS 2 side and 8 exist only in L1.

The mismatch is not only naming:

- Path A has one composite action, `mission-control.deliver_tool`, which
  encapsulates the whole delivery inside the state machine. Path B has
  `pick_tool`, `verify_grasp`, `handover_tool`, `mission.terminate` — the model
  decides each subgoal.
- Path B has recovery primitives (`navigation.reposition`,
  `perception.request_tool_detection`) that do not exist as callable path A
  actions.
- Path A declares `perception.detect_tool_yolov8` but marks it non-callable; in
  path B the equivalent is `perception.request_tool_detection`, whose result
  arrives through the next observation.

Therefore: a plan written for path A cannot be replayed by path B, and a path B
trajectory cannot be executed by path A without a translation layer that
currently does not exist.

## 6. Defects found while tracing

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D1 | Timeout is treated as failure, not unknown | `ros_tool_executor.py:356-359` | a cancelled goal may still have executed; the code reports an error and the plan stops |
| D2 | No command identity | `ros_tool_executor.py:287` (per-plan `trace_id` only) | a retried step is indistinguishable from a new one |
| D3 | Topic publish reports success unconditionally | `ros_tool_executor.py:384-394` | "published" is not a receipt; no subscriber or effect verification |
| D4 | Action completion = success | `ros_tool_executor.py:363` (`status == 4`) | action SUCCEEDED is reported as tool success; task success is a different claim |
| D5 | Plan aborts by `break` | `ros_tool_executor.py:321-322` | no cancellation of an in-flight goal beyond the timeout path, no compensation, no record of skipped steps |
| D6 | Stop is an agent action | `ros_tool_executor.py:144` (`emergency = command == "system_stop"`) | the emergency flag is derived from the model's chosen command; no independent supervisor |
| D7 | Global timeout only | `ros_tool_executor.py:415` | one `--timeout-sec` for every transport and tool |
| D8 | Two validators drift | `ros_tool_executor.py:87-122` vs `protocol.py:172-189` | an action can be legal in one path and illegal in the other |
| D9 | Training env outcome is the only ground truth for eval | `multi_turn_env.py:81-83` | path A has no equivalent outcome object; a mission run leaves no evaluable record |

### Additional defects (verified in this pass)

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D10 | Unknown navigation backend silently falls back to a simulation that always succeeds | `nav_skill_server.py:85-91`, simulated path `:93-115` (`goal_handle.succeed()` at `:114`) | a typo in the `backend` parameter reports "Reached <station>" with no motion |
| D11 | Handover is a fixed simulated step, then success | `mission_state_machine.py:147-156` | the mission's only proof of delivery is a sleep and an unconditional `succeed()` |
| D12 | Confirmation is a 0.18 s simulated step | `mission_state_machine.py:121-127` | "requires confirmation" carries no operator interaction |
| D13 | The plan author controls whether confirmation is required | `ros_tool_executor.py:137` (`safety.get("requires_handover_confirmation", True)`) | an LLM-authored `safety` block can switch the requirement off |
| D14 | Manipulator pause is an unbounded wait that only exits on cancel or stop | `arm_skill_server.py:93-105` | if neither signal arrives the goal holds the action server indefinitely |
| D15 | The arm and navigation servers are entirely simulated in the audited paths | `arm_skill_server.py:77-115`, `nav_skill_server.py:93-115` | path A's "success" is a simulation result; nothing here proves a physical effect |
| D16 | Cancel is checked before the flow, not inside the core steps | `mission_state_machine.py:113` (single pre-check) | a cancel arriving mid-delivery is not propagated to the navigation/manipulation goal in this code path (**UNVERIFIED** whether the action clients cancel) |

## 7. What path A is missing to be closed-loop

Path A stops being closed-loop at `execute_plan` (`ros_tool_executor.py:289`):
the loop is over a pre-computed list, and the only observation available after a
step is the ROS action result, which is not fed back into any decision. There is
no step boundary at which a policy could observe, decide, and act again.
