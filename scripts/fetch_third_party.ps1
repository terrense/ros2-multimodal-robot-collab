$ErrorActionPreference = "Stop"

$paths = @("third_party/ultralytics", "third_party/VINS-Mono", "third_party/openpose")
foreach ($path in $paths) {
    if (-not (Test-Path $path)) {
        throw "Missing $path"
    }
}

Write-Host "Vendored third-party sources are present:"
Write-Host "  - third_party/ultralytics"
Write-Host "  - third_party/VINS-Mono"
Write-Host "  - third_party/openpose"
