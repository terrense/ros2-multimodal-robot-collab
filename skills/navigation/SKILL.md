---
name: navigation
description: Use this skill when an Agent needs a mobile robot to move to a named station, workspace, tool shelf, delivery area, or recovery waypoint through ROS2/Nav2.
---

# Navigation Skill

Call the `/skills/navigate_to_station` Action for station-level movement.

Required inputs:

- `station_id`: named station in `config/stations.yaml`
- `reason`: short task reason for logs and operator feedback

Action:

- Type: `robot_collab_interfaces/action/NavigateToStation`
- Name: `/skills/navigate_to_station`

Planner rules:

- Do not call this skill before identity verification for user-requested delivery tasks.
- Use named stations, not raw map coordinates, unless a NavigationAgent has validated the pose.
- On failure, request a recovery waypoint or ask HRI for human assistance.

