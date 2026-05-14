#!/usr/bin/env bash
set -euo pipefail

for path in third_party/ultralytics third_party/VINS-Mono third_party/openpose; do
  if [ ! -d "$path" ]; then
    echo "Missing $path" >&2
    exit 1
  fi
done

echo "Vendored third-party sources are present:"
echo "  - third_party/ultralytics"
echo "  - third_party/VINS-Mono"
echo "  - third_party/openpose"
