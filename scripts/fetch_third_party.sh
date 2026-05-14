#!/usr/bin/env bash
set -euo pipefail

git submodule update --init --recursive --depth 1

echo "Third-party sources are ready:"
echo "  - third_party/ultralytics"
echo "  - third_party/VINS-Mono"

