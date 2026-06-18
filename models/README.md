# models/

Fetched assets for the perception chain. Nothing here is committed (see `.gitignore`).

Run from the repo root:

```bash
bash scripts/fetch_models.sh      # Linux / WSL
# or
pwsh scripts/fetch_models.ps1     # Windows PowerShell
```

This downloads:

| File | Source | Used by |
| --- | --- | --- |
| `yolov8n.pt` | Ultralytics COCO-pretrained weights | `yolov8_tool_detector_node` |
| `coco_sample.jpg` | A real photo (Ultralytics `bus.jpg`) | YOLOv8 static-image mode |

The demo runs stock COCO weights on purpose. Tool-specific accuracy is a later
phase; the goal here is a working end-to-end perception → mission chain. The
`class_to_tool_json` `"*"` wildcard maps any detected class onto the requested
tool id so the mission unblocks.
