# Third-Party Components

This project vendors third-party robotics and perception source snapshots under `third_party/`. The ROS2 packages in `src/` are project code; third-party directories keep their own authorship, notices, and licenses.

## Components

| Component | Path | Source | License | Usage |
| --- | --- | --- | --- | --- |
| Ultralytics YOLO | `third_party/ultralytics` | https://github.com/ultralytics/ultralytics | AGPL-3.0 or Ultralytics Enterprise License | YOLOv8 tool detection backend |
| VINS-Mono | `third_party/VINS-Mono` | https://github.com/HKUST-Aerial-Robotics/VINS-Mono | GPLv3 | Monocular visual-inertial odometry reference implementation |
| OpenPose | `third_party/openpose` | https://github.com/CMU-Perceptual-Computing-Lab/openpose | CMU non-commercial research license | Hand keypoint detection for gesture control |
| OpenPose Caffe fork | `third_party/openpose/3rdparty/caffe` | https://github.com/CMU-Perceptual-Computing-Lab/caffe | See upstream license | OpenPose build dependency |
| pybind11 | `third_party/openpose/3rdparty/pybind11` | https://github.com/pybind/pybind11 | See upstream license | OpenPose Python binding dependency |

Current pinned source snapshot commits:

- Ultralytics: `aae4c3b`
- VINS-Mono: `90dabb5`
- OpenPose: `5c5d965`
- OpenPose Caffe fork: `2d4bf54`
- pybind11: `00a9c62`

## Notes

- The root MIT license applies to the original ROS2 integration code in this repository.
- Third-party source snapshots are governed by their own licenses.
- If YOLOv8, VINS-Mono, or OpenPose code is redistributed, modified, or deployed in a product, review the corresponding AGPL/GPL/non-commercial research obligations carefully.
- For closed-source or commercial deployments of Ultralytics YOLO, use the appropriate Ultralytics Enterprise License.
- OpenPose is free for non-commercial research use; commercial use requires a separate license from the rights holder.

## Source Layout

The source code is committed directly under `third_party/`, not referenced as Git submodules.
