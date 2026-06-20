# models/

Local weights for the perception chain. Nothing here is committed (see `.gitignore`).

The real detector (`yolov8_tool_detector_node`, enabled with `use_yolo:=true`)
loads `models/yolov8n.pt` — the stock Ultralytics COCO-pretrained weights.

Fetch it once from the repo root:

```bash
pip install ultralytics
python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"   # downloads to CWD
mv yolov8n.pt models/                                            # if it landed elsewhere
```

The demo runs stock COCO weights on purpose: COCO cannot see project tools
(hex keys, wrenches), so `demo_params.yaml` maps a few easy-to-place COCO
classes (bottle, cup, cell phone, ...) onto the requested tool id. Drop one of
those objects in front of the robot camera and the mission unblocks. Real
tool-specific weights are a later phase.
