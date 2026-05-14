---
name: manipulation
description: Use this skill when an Agent needs the robot arm to pick, transfer, place, hand over, or recover a tool using ROS2 arm control or MoveIt2.
---

# Manipulation Skill

Call `/skills/pick_and_place` for grasp and placement tasks.

Required inputs:

- `tool_id`: requested tool id
- `source_frame`: frame for the detected tool pose
- `target_frame`: frame for the delivery or staging pose

Action:

- Type: `robot_collab_interfaces/action/PickAndPlace`
- Name: `/skills/pick_and_place`

Planner rules:

- Do not call this skill until navigation has reached the manipulation workspace.
- Tool pose must be fresh enough for the configured scene.
- If grasp fails, request a new perception update before retrying.

