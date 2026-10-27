<#
.SYNOPSIS
  Package the Electron shell into a Windows installer.
  Output: EVA/Build/EVA Setup.exe

.PARAMETER Obfuscate
  Obfuscate the React renderer bundle (control-flow flattening, encoded strings)
  before packaging. OFF by default: default builds ship readable, auditable
  renderer code. Does not touch main.js or the Python backend.

.NOTES
  Prerequisites:
  - build-frontend.ps1 must have run (EVA/frontend/dist/ must exist)
  - build-backend.ps1 must have run (EVA/backend/backend-dist/eva-backend/ must exist)
#>
param(
  [string]$ElectronDir  = "$PSScriptRoot\..",
  [string]$FrontendDist = "$PSScriptRoot\..\..\frontend\dist",
  [string]$BackendDist  = "$PSScriptRoot\..\..\backend\backend-dist\eva-backend",
  [switch]$Obfuscate
)

# Continue: npm/electron-builder write to stderr (deprecation warnings etc.)
# which PS5.1 wraps as NativeCommandError with Stop.
$ErrorActionPreference = 'Continue'
$ElectronDir  = Resolve-Path $ElectronDir
$FrontendDist = [IO.Path]::GetFullPath($FrontendDist)
$BackendDist  = [IO.Path]::GetFullPath($BackendDist)

Write-Host "`n==============================" -ForegroundColor Cyan
Write-Host "  EVA Electron Packaging" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan

# ── Preflight checks ──────────────────────────────────────────────────────────
Write-Host "`n Preflight checks..." -ForegroundColor Cyan

if (-not (Test-Path $FrontendDist)) {
  Write-Host "  ERROR: $FrontendDist not found. Run build-frontend.ps1 first." -ForegroundColor Red
  exit 1
}
if (-not (Test-Path (Join-Path $BackendDist "eva-backend.exe"))) {
  Write-Host "  ERROR: eva-backend.exe not found in $BackendDist. Run build-backend.ps1 first." -ForegroundColor Red
  exit 1
}
Write-Host "  Frontend dist : OK" -ForegroundColor Gray
Write-Host "  Backend dist  : OK" -ForegroundColor Gray

# ── Kill processes that lock Build/ files ────────────────────────────────────
# EVA.exe, electron.exe and eva-backend.exe hold handles on files inside Build/.
# Windows Defender also scans large exes on creation — stop everything we can.
Write-Host "`n Killing processes that may lock Build/ files..." -ForegroundColor Gray
Get-Process -Name "EVA","EVA.exe","eva-backend","electron" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2   # brief pause so handles are released

# ── Clean previous Build/ output ─────────────────────────────────────────────
# Must be done explicitly: clean:false in CI preserves Build/win-unpacked/ between
# runs, causing electron-builder to accumulate backend resources (+439MB per run).
# Use cmd /c rd /s /q — bypasses PowerShell file-handle quirks on large directories.
$installerOutput = [IO.Path]::GetFullPath((Join-Path $ElectronDir "..\installer"))
if (Test-Path $installerOutput) {
  Write-Host "`n Cleaning previous installer/ output..." -ForegroundColor Gray
  cmd /c rd /s /q "$installerOutput"
  if (Test-Path $installerOutput) {
    Write-Host "  ERROR: Could not delete installer/ - files still locked after process kill." -ForegroundColor Red
    Write-Host "  Run this manually on the runner machine, then re-tag:" -ForegroundColor Yellow
    Write-Host "    cmd /c rd /s /q `"$installerOutput`"" -ForegroundColor Yellow
    exit 1
  }
  Write-Host "  installer/ cleaned." -ForegroundColor Gray
}

# ── Install Electron deps ─────────────────────────────────────────────────────
Write-Host "`n Installing Electron dependencies..." -ForegroundColor Cyan
Set-Location $ElectronDir
# npm ci installs exactly what package-lock.json says (and fails if package.json
# and the lock disagree). It wipes node_modules, so it only runs when the
# lock/package.json changed since the last successful install (stamp file).
$npmStamp = Join-Path $ElectronDir "node_modules\.eva-install-stamp"
$npmKey = (Get-FileHash (Join-Path $ElectronDir "package-lock.json") -Algorithm SHA256).Hash + "|" +
          (Get-FileHash (Join-Path $ElectronDir "package.json") -Algorithm SHA256).Hash
if ((Test-Path $npmStamp) -and ((Get-Content $npmStamp -Raw).Trim() -eq $npmKey)) {
  Write-Host "  node_modules matches package-lock.json, skipping install." -ForegroundColor Gray
} else {
  npm ci
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: npm ci failed (exit $LASTEXITCODE). If package.json changed, run 'npm install' in build/ and commit package-lock.json." -ForegroundColor Red
    exit 1
  }
  $npmKey | Out-File $npmStamp -Encoding ascii
}

# ── Obfuscate renderer bundle (optional, off by default) ─────────────────────
# Runs here (after npm ci) so javascript-obfuscator is available. Operates on
# frontend/dist/assets which build-frontend.ps1 already produced.
# A stamp (tied to dist/index.html, which Vite rewrites on every build) records
# that the current dist was obfuscated, so a default build can never silently
# ship a dist left obfuscated by an earlier -Obfuscate run (e.g. -SkipFrontend),
# and -Obfuscate never obfuscates the same bundle twice.
$obfStamp = Join-Path $ElectronDir "node_modules\.eva-obfuscated-dist"
$distId = (Get-Item (Join-Path $FrontendDist "index.html")).LastWriteTimeUtc.Ticks.ToString()
$alreadyObfuscated = (Test-Path $obfStamp) -and ((Get-Content $obfStamp -Raw).Trim() -eq $distId)

if ($Obfuscate) {
  if ($alreadyObfuscated) {
    Write-Host "`n Renderer bundle is already obfuscated - skipping." -ForegroundColor Gray
  } else {
    Write-Host "`n Obfuscating renderer bundle (-Obfuscate)..." -ForegroundColor Cyan
    node "$ElectronDir\obfuscate-renderer.js"
    if ($LASTEXITCODE -ne 0) {
      Write-Host "  ERROR: obfuscation failed (exit $LASTEXITCODE)" -ForegroundColor Red
      exit 1
    }
    $distId | Out-File $obfStamp -Encoding ascii
    Write-Host "  Renderer bundle obfuscated." -ForegroundColor Green
  }
} elseif ($alreadyObfuscated) {
  Write-Host "`n  ERROR: frontend/dist is obfuscated from an earlier -Obfuscate build." -ForegroundColor Red
  Write-Host "  Re-run build-frontend.ps1 for a readable build, or pass -Obfuscate." -ForegroundColor Yellow
  exit 1
} else {
  Write-Host "`n Renderer obfuscation: off (readable bundle). Use -Obfuscate to enable." -ForegroundColor Gray
}

# ── Run electron-builder ──────────────────────────────────────────────────────
Write-Host "`n Running electron-builder (Windows NSIS installer)..." -ForegroundColor Cyan
npm run "build:win"
if ($LASTEXITCODE -ne 0) {
  Write-Host "`n  electron-builder failed (exit $LASTEXITCODE)" -ForegroundColor Red
  exit 1
}

$buildDir = Resolve-Path (Join-Path $ElectronDir "..\installer")
Write-Host "`n  Packaging complete." -ForegroundColor Green
Write-Host "  Output: $buildDir" -ForegroundColor Green
Get-ChildItem $buildDir -Filter "*.exe" | Select-Object Name, @{N='Size(MB)';E={[math]::Round($_.Length/1MB,1)}}
