# Build Motherbrain.exe (classic) and Occhialini.exe (modern).
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
param(
    [switch]$SkipInstall,
    [switch]$SkipTests,
    [switch]$SkipSmoke,
    [switch]$ClassicOnly,
    [switch]$ModernOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Repo root: $Root"

if (-not $SkipInstall) {
    Write-Host "Installing pinned desktop dependencies..."
    python -m pip install -r requirements-desktop.txt | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
}

python scripts/generate_brand_assets.py
if ($LASTEXITCODE -ne 0) { throw "Brand asset generation failed" }

if (-not $SkipTests) {
    Write-Host "Running focused desktop tests..."
    python -m pytest tests -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
}

if (-not $ModernOnly) {
    Write-Host "Building Motherbrain.exe (classic workstation)..."
    python -m PyInstaller --noconfirm --clean motherbrain.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for motherbrain.spec" }
    $Classic = Join-Path $Root "dist\Motherbrain.exe"
    if (-not (Test-Path $Classic)) { throw "Missing output: $Classic" }
    Write-Host "OK: $Classic"
}

if (-not $ClassicOnly) {
    Write-Host "Building Occhialini.exe (modern desktop)..."
    python -m PyInstaller --noconfirm --clean occhialini.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for occhialini.spec" }
    $Modern = Join-Path $Root "dist\Occhialini.exe"
    if (-not (Test-Path $Modern)) { throw "Missing output: $Modern" }
    Write-Host "OK: $Modern"
}

if (-not $SkipSmoke) {
    function Invoke-DesktopSmoke([string]$Path, [string]$SmokeVariable) {
        Write-Host "Smoke testing $([IO.Path]::GetFileName($Path))..."
        $PreviousSkip = $env:MOTHERBRAIN_SKIP_PIN
        $PreviousSmoke = [Environment]::GetEnvironmentVariable($SmokeVariable, "Process")
        try {
            $env:MOTHERBRAIN_SKIP_PIN = "1"
            [Environment]::SetEnvironmentVariable($SmokeVariable, "1", "Process")
            $Process = Start-Process -FilePath $Path -PassThru
            if (-not $Process.WaitForExit(45000)) {
                $Process.Kill()
                throw "Smoke test timed out: $Path"
            }
            if ($Process.ExitCode -ne 0) {
                throw "Smoke test failed with exit code $($Process.ExitCode): $Path"
            }
        }
        finally {
            $env:MOTHERBRAIN_SKIP_PIN = $PreviousSkip
            [Environment]::SetEnvironmentVariable($SmokeVariable, $PreviousSmoke, "Process")
        }
    }
    if (-not $ModernOnly) { Invoke-DesktopSmoke (Join-Path $Root "dist\Motherbrain.exe") "MOTHERBRAIN_SMOKE" }
    if (-not $ClassicOnly) { Invoke-DesktopSmoke (Join-Path $Root "dist\Occhialini.exe") "OCCHIALINI_SMOKE" }
}

$Artifacts = @()
if (Test-Path (Join-Path $Root "dist\Motherbrain.exe")) { $Artifacts += (Join-Path $Root "dist\Motherbrain.exe") }
if (Test-Path (Join-Path $Root "dist\Occhialini.exe")) { $Artifacts += (Join-Path $Root "dist\Occhialini.exe") }
$Checksums = $Artifacts | ForEach-Object {
    $Hash = Get-FileHash -Algorithm SHA256 $_
    "$($Hash.Hash.ToLower())  $([IO.Path]::GetFileName($_))"
}
$Checksums | Set-Content -Encoding ascii (Join-Path $Root "dist\SHA256SUMS.txt")

Write-Host ""
Write-Host "User data still lives in %USERPROFILE%\.motherbrain (not inside the exe)."
Write-Host "Do not commit dist/ or build/."
