# Fetch pretrained YOLOv8 weights and a real sample image for the demo.
# Windows/PowerShell companion to scripts/fetch_models.sh.
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$ModelsDir = Join-Path $RootDir "models"
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null

$Weights = Join-Path $ModelsDir "yolov8n.pt"
$Sample = Join-Path $ModelsDir "coco_sample.jpg"

function Get-File($Out, [string[]]$Urls) {
    foreach ($url in $Urls) {
        Write-Host "Downloading $(Split-Path -Leaf $Out) from $url"
        try {
            Invoke-WebRequest -Uri $url -OutFile $Out -UseBasicParsing
            return $true
        } catch {
            Write-Host "  failed, trying next mirror ..."
        }
    }
    return $false
}

if (Test-Path $Weights) {
    Write-Host "[OK] weights already present: $Weights"
} elseif (Get-File $Weights @(
        "https://github.com/ultralytics/assets/releases/latest/download/yolov8n.pt",
        "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n.pt")) {
    Write-Host "[OK] weights -> $Weights"
} else {
    Write-Host "[WARN] could not download yolov8n.pt; Ultralytics will auto-download at first run."
}

if (Test-Path $Sample) {
    Write-Host "[OK] sample image already present: $Sample"
} elseif (Get-File $Sample @(
        "https://ultralytics.com/images/bus.jpg",
        "https://raw.githubusercontent.com/ultralytics/ultralytics/main/ultralytics/assets/bus.jpg")) {
    Write-Host "[OK] sample image -> $Sample"
} else {
    throw "Could not download the sample image. Static-image YOLO mode needs models/coco_sample.jpg."
}

Write-Host ""
Write-Host "Done. The demo launch defaults to tool_image:=models/coco_sample.jpg."
