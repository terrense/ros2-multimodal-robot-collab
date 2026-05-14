---
name: system-state
description: Use this skill when an Agent needs current ROS2 system status, mission state, active warnings, node availability, or recovery context.
---

# System State Skill

Call `/system/query_state` before recovery, retry, or user-facing status summaries.

Service:

- Type: `robot_collab_interfaces/srv/QuerySystemState`
- Name: `/system/query_state`

Planner rules:

- Query state before retrying failed navigation or manipulation.
- Include active warnings in HRI summaries.
- If critical nodes are unavailable, stop dispatching new movement actions.
- If an emergency gesture was observed, require an explicit `arm_start` resume command before new manipulation.
