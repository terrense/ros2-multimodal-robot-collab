#!/usr/bin/env bash
# Fetch the pretrained YOLOv8 weights and a real sample image used by the
# end-to-end demo. Accuracy is not the goal here: stock COCO weights plus a real
# photo are enough to drive the full perception -> mission chain.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$ROOT_DIR/models"
mkdir -p "$MODELS_DIR"

WEIGHTS="$MODELS_DIR/yolov8n.pt"
SAMPLE="$MODELS_DIR/coco_sample.jpg"

download() {
  local out="$1"
  shift
  for url in "$@"; do
    echo "Downloading $(basename "$out") from $url"
    if command -v curl >/dev/null 2>&1; then
      if curl -fL --retry 3 -o "$out" "$url"; then return 0; fi
    elif command -v wget >/dev/null 2>&1; then
      if wget -O "$out" "$url"; then return 0; fi
    else
      echo "Neither curl nor wget is available." >&2
      return 1
    fi
    echo "  failed, trying next mirror ..." >&2
  done
  return 1
}

# 1) Pretrained YOLOv8n COCO weights.
if [ -f "$WEIGHTS" ]; then
  echo "[OK] weights already present: $WEIGHTS"
elif download "$WEIGHTS" \
    "https://github.com/ultralytics/assets/releases/latest/download/yolov8n.pt" \
    "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt"; then
  echo "[OK] weights -> $WEIGHTS"
else
  echo "[WARN] could not download yolov8n.pt directly." >&2
  echo "       Ultralytics will auto-download it at first run if the network is reachable." >&2
fi

# 2) Real sample image for YOLOv8 static-image mode.
if [ -f "$SAMPLE" ]; then
  echo "[OK] sample image already present: $SAMPLE"
elif download "$SAMPLE" \
    "https://ultralytics.com/images/bus.jpg" \
    "https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg"; then
  echo "[OK] sample image -> $SAMPLE"
else
  echo "[FAIL] could not download the sample image. Static-image YOLO mode needs models/coco_sample.jpg." >&2
  exit 1
fi

echo
echo "Done. The demo launch defaults to tool_image:=models/coco_sample.jpg."
