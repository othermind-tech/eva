<#
.SYNOPSIS
  Full EVA build pipeline: Frontend → Backend → Electron installer

.DESCRIPTION
  Runs all three build steps in sequence and reports total time.
  Use -SkipFrontend or -SkipBackend to skip steps when only one component changed.

.EXAMPLE
  # Full build (first time or after changes to both frontend and backend)
  .\build-all.ps1

  # Backend changed, frontend unchanged
  .\build-all.ps1 -SkipFrontend

  # Frontend/Electron changed, backend unchanged
  .\build-all.ps1 -SkipBackend

  # Obfuscate the renderer bundle (default builds ship readable code)
  .\build-all.ps1 -Obfuscate

.NOTES
  For PyInstaller output, pipe through Tee-Object to see live progress:
    .\build-all.ps1 2>&1 | Tee-Object -FilePath build-all.log
#>
param(
  [switch]$SkipFrontend,
  [switch]$SkipBackend,
  [switch]$Obfuscate      # obfuscate the renderer bundle (off by default)
)

$ErrorActionPreference = 'Stop'
$start = Get-Date

Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  EVA Full Build Pipeline"               -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm')" -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
if ($SkipFrontend) { Write-Host "  [--SkipFrontend]" -ForegroundColor DarkGray }
if ($SkipBackend)  { Write-Host "  [--SkipBackend]"  -ForegroundColor DarkGray }
if ($Obfuscate)    { Write-Host "  [--Obfuscate]"    -ForegroundColor DarkGray }

# ── Step 1: Frontend ──────────────────────────────────────────────────────────
if (-not $SkipFrontend) {
  Write-Host "`n  Step 1/3 - Frontend (Vite)" -ForegroundColor Yellow
  & "$PSScriptRoot\build-frontend.ps1"
  if ($LASTEXITCODE -ne 0) { Write-Host "  Build step failed (exit $LASTEXITCODE) - stopping." -ForegroundColor Red; exit $LASTEXITCODE }
} else {
  Write-Host "`n  Step 1/3 - Frontend SKIPPED (using existing dist/)" -ForegroundColor DarkGray
}

# ── Step 2: Backend ───────────────────────────────────────────────────────────
if (-not $SkipBackend) {
  Write-Host "`n  Step 2/3 - Backend (PyInstaller)" -ForegroundColor Yellow
  & "$PSScriptRoot\build-backend.ps1"
  if ($LASTEXITCODE -ne 0) { Write-Host "  Build step failed (exit $LASTEXITCODE) - stopping." -ForegroundColor Red; exit $LASTEXITCODE }
} else {
  Write-Host "`n  Step 2/3 - Backend SKIPPED (using existing backend-dist/)" -ForegroundColor DarkGray
}

# ── Step 3: Electron installer ───────────────────────────────────────────────
Write-Host "`n  Step 3/3 - Electron packaging" -ForegroundColor Yellow
& "$PSScriptRoot\build-electron.ps1" -Obfuscate:$Obfuscate
if ($LASTEXITCODE -ne 0) { Write-Host "  Build step failed (exit $LASTEXITCODE) - stopping." -ForegroundColor Red; exit $LASTEXITCODE }

# ── Summary ───────────────────────────────────────────────────────────────────
$elapsed = [math]::Round(((Get-Date) - $start).TotalMinutes, 1)
Write-Host "`n=======================================" -ForegroundColor Green
Write-Host "  Build complete in $elapsed minutes"    -ForegroundColor Green
Write-Host "  Installer: EVA\Build\EVA Setup.exe"    -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
