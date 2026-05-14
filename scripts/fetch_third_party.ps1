$ErrorActionPreference = "Stop"

git submodule update --init --recursive --depth 1

Write-Host "Third-party sources are ready:"
Write-Host "  - third_party/ultralytics"
Write-Host "  - third_party/VINS-Mono"

