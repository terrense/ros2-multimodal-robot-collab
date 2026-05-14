---
name: perception
description: Use this skill when an Agent needs tool detection, operator identity verification, gesture interpretation, or perception confidence checks in the ROS2 robot collaboration system.
---

# Perception Skill

Use perception topics and actions to ground plans in current sensor data.

APIs:

- Tool detections: `/perception/tool_detections`, `robot_collab_interfaces/msg/ToolDetection`
- Identity verification: `/skills/verify_operator`, `robot_collab_interfaces/action/VerifyOperator`
- Gesture command stream: `/hri/gesture_command`, `robot_collab_interfaces/msg/GestureCommand`
- YOLOv8 detector adapter: `yolov8_tool_detector_node`

Gesture mapping:

- `fist` -> `arm_pause`
- `palm` -> `system_stop`
- `thumb_up` -> `arm_start`

Planner rules:

- Require confidence above the configured threshold before sending a pick request.
- If multiple tool candidates are present, choose the one closest to the requested station or ask HRI for clarification.
- If identity verification fails, stop the mission and announce the rejection.
- Treat `system_stop` as a safety-critical command.
