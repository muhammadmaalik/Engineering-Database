# Build Motherbrain.exe with PyInstaller (one-file, windowed).
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
# Optional second target:
#   powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -IncludeSyncClient

param(
    [switch]$IncludeSyncClient,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Repo root: $Root"

if (-not $SkipInstall) {
    Write-Host "Ensuring PyInstaller..."
    python -m pip install --upgrade pyinstaller | Out-Host
}

Write-Host "Building Motherbrain.exe (workstation)..."
python -m PyInstaller --noconfirm --clean motherbrain.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for motherbrain.spec" }

$Primary = Join-Path $Root "dist\Motherbrain.exe"
if (-not (Test-Path $Primary)) { throw "Missing output: $Primary" }
Write-Host "OK: $Primary"

if ($IncludeSyncClient) {
    Write-Host "Building SyncClient.exe..."
    python -m PyInstaller --noconfirm --clean `
        --name SyncClient `
        --windowed `
        --onefile `
        --paths $Root `
        --add-data "templates\web_companion.html;templates" `
        --collect-submodules core `
        --collect-submodules tools `
        sync_client.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for sync_client.py" }
    Write-Host "OK: $(Join-Path $Root 'dist\SyncClient.exe')"
}

Write-Host ""
Write-Host "User data still lives in %USERPROFILE%\.motherbrain (not inside the exe)."
Write-Host "Do not commit dist/ or build/."
