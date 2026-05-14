---
name: mission-control
description: Use this skill when an Agent needs to start, monitor, cancel, or recover a complete ROS2 tool delivery mission involving identity verification, perception, navigation, manipulation, and HRI feedback.
---

# Mission Control Skill

Call the `/mission/deliver_tool` ROS2 Action when the user intent is a complete delivery task.

Required inputs:

- `tool_id`: stable tool identifier, such as `hex_key_3mm`
- `target_station`: known station id, such as `station_a`
- `operator_id`: verified or claimed operator id

Action:

- Type: `robot_collab_interfaces/action/DeliverTool`
- Name: `/mission/deliver_tool`

Planner rules:

- Verify the operator before dispatching motion.
- Prefer this skill over direct navigation/manipulation calls for normal user-facing tasks.
- If feedback state enters `RECOVER_NAVIGATION` or `RECOVER_MANIPULATION`, ask System State Skill before retrying.
- If the user cancels, cancel the active action goal and use HRI Skill to acknowledge.

