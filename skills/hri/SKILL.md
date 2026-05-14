---
name: hri
description: Use this skill when an Agent needs to speak to the operator through TTS, interpret ASR text, request clarification, or confirm a robot delivery action.
---

# HRI Skill

HRI bridges human speech, gesture, and robot task status.

Topics:

- ASR input: `/hri/asr_text`, `std_msgs/msg/String`
- TTS output: `/hri/tts_text`, `std_msgs/msg/String`
- Gesture input: `/hri/gesture_command`, `robot_collab_interfaces/msg/GestureCommand`
- Arm control output: `/arm/control_command`, `robot_collab_interfaces/msg/ArmControlCommand`
- Mission events: `/mission/events`, `robot_collab_interfaces/msg/MissionEvent`

Gesture vocabulary:

- Fist means pause the manipulator.
- Open palm means stop and request shutdown/safe-stop behavior.
- Thumb up means start or resume work.

Planner rules:

- Keep spoken output short and action-oriented.
- Ask clarification when the tool id, station id, or operator id is ambiguous.
- Ask confirmation before entering a shared human workspace.
- Convert mission failures into helpful next steps, not raw stack traces.
