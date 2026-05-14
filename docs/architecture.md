# System Architecture

## Goals

The system is designed around replaceable ROS2 capabilities rather than one monolithic robot program:

- keep robot base, arm, cameras, HRI, perception, and task planning as separate nodes;
- expose long-running work as Actions so navigation and manipulation can stream feedback and be canceled;
- expose quick queries as Services;
- publish mission events and perception facts on Topics;
- let an upper-level Agent plan through stable skill contracts instead of hardware-specific APIs.

## Runtime Node Graph

| Area | Node | Main API | Responsibility |
| --- | --- | --- | --- |
| Mission | `mission_state_machine` | `/mission/deliver_tool` action | Owns task state, retries, fallback, progress feedback |
| Navigation | `nav_skill_server` | `/skills/navigate_to_station` action | Wraps Nav2 or simulation navigation |
| Manipulation | `arm_skill_server` | `/skills/pick_and_place` action | Wraps MoveIt2/vendor arm control |
| Perception | `tool_detector_node` | `/perception/tool_detections` topic | Publishes tool pose candidates |
| Perception | `face_auth_node` | `/skills/verify_operator` action | Verifies operator authorization |
| Perception | `gesture_recognizer_node` | `/hri/gesture_command` topic | Publishes gesture commands |
| HRI | `voice_gateway_node` | `/hri/asr_text`, `/hri/tts_text` topics | Bridges ASR/TTS and mission events |
| Agent | `agent_gateway_node` | `/agent/command_text`, `/system/query_state` | Parses planner/HRI commands into ROS2 actions |

## Data Flow

1. Operator speaks or gestures.
2. HRI gateway publishes a normalized command.
3. Agent gateway parses the command or receives a structured planner request.
4. Mission state machine verifies identity.
5. Tool perception provides the target candidate.
6. Navigation skill moves to the workspace/tool station.
7. Manipulation skill picks the tool and places it in the delivery zone.
8. Mission state machine publishes completion/failure events.
9. HRI gateway announces progress through TTS.

## Failure Handling

The mission state machine treats each capability as fallible:

- identity failure stops immediately and announces rejection;
- missing tool detection requests a perception retry;
- navigation failure moves into `RECOVER_NAVIGATION` and can retry or abort;
- manipulation failure moves into `RECOVER_MANIPULATION` and can retry grasp/place;
- user cancellation propagates through active Actions.

## Multi-Agent Design

Use a supervisor-worker model:

| Agent | Scope | Inputs | Outputs |
| --- | --- | --- | --- |
| `MissionSupervisor` | Task decomposition, safety checks, final decisions | User intent, system state, skill results | Ordered skill calls, abort/retry decisions |
| `PerceptionAgent` | Tool, face, gesture interpretation | Images, detections, operator registry | Tool candidates, operator identity, gesture intent |
| `NavigationAgent` | Route and station selection | Map, station registry, robot pose | Station target, recovery suggestion |
| `ManipulationAgent` | Grasp/place strategy | Tool pose, arm state, gripper state | Pick/place request, grasp metadata |
| `HriAgent` | Conversation, confirmation, ASR/TTS wording | ASR text, mission events | TTS text, clarification requests |
| `SafetyAgent` | Guardrails and preflight checks | Mission plan, zone state, auth result | Allow/deny/retry decision |

The first implementation can run all agents in one process behind `agent_gateway_node`. When latency or ownership matters, each agent can become a separate process or remote service while preserving the same ROS2 skill contracts.

