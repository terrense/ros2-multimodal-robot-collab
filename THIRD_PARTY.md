# Third-Party Components

This project integrates third-party robotics and perception projects through Git submodules under `third_party/`. The ROS2 packages in `src/` are project code; third-party repositories keep their own authorship, history, and licenses.

## Components

| Component | Path | Source | License | Usage |
| --- | --- | --- | --- | --- |
| Ultralytics YOLO | `third_party/ultralytics` | https://github.com/ultralytics/ultralytics | AGPL-3.0 or Ultralytics Enterprise License | YOLOv8 tool detection backend |
| VINS-Mono | `third_party/VINS-Mono` | https://github.com/HKUST-Aerial-Robotics/VINS-Mono | GPLv3 | Monocular visual-inertial odometry reference implementation |

Current pinned submodule commits:

- Ultralytics: `aae4c3b`
- VINS-Mono: `90dabb5`

## Notes

- The root MIT license applies to the original ROS2 integration code in this repository.
- Third-party submodules are governed by their own licenses.
- If YOLOv8 or VINS-Mono code is redistributed, modified, or deployed in a product, review the corresponding AGPL/GPL obligations carefully.
- For closed-source or commercial deployments of Ultralytics YOLO, use the appropriate Ultralytics Enterprise License.

## Initialize Submodules

```bash
git submodule update --init --recursive --depth 1
```
