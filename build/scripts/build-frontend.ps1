<#
.SYNOPSIS
  Build the EVA React frontend for production.
  Output: EVA/frontend/dist/

.NOTES
  Runs `npm run build` (`tsc -b && vite build`) — the type-check is no longer
  bypassed (FE-6.1, 2026-08-19). Previously used `npx vite build` directly to
  skip `tsc -b` due to pre-existing TypeScript errors; those were fixed and
  the bypass removed so future type errors are caught here instead of
  silently drifting again.

  .env.production in the frontend folder sets VITE_API_BASE=http://127.0.0.1:8000
  so the production bundle calls the backend directly on port 8000.
#>
param(
  [string]$FrontendDir = "$PSScriptRoot\..\..\frontend"
)

# Continue: PS5.1 wraps native stderr (npm warnings, vite deprecation notices)
# as NativeCommandError with Stop. Use explicit exit-code checks instead.
$ErrorActionPreference = 'Continue'
$FrontendDir = Resolve-Path $FrontendDir

Write-Host "`n==============================" -ForegroundColor Cyan
Write-Host "  EVA Frontend Build (Vite)" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "  Source: $FrontendDir" -ForegroundColor Gray

Write-Host "`n[1/2] Installing frontend dependencies..." -ForegroundColor Cyan
Set-Location $FrontendDir
# npm ci installs exactly what package-lock.json says (and fails if package.json and the lock
# disagree). It wipes node_modules, so it only runs when the lock/package.json changed since the
# last successful install (stamp file); otherwise the existing install is reused.
$npmStamp = Join-Path $FrontendDir "node_modules\.eva-install-stamp"
$npmKey = (Get-FileHash (Join-Path $FrontendDir "package-lock.json") -Algorithm SHA256).Hash + "|" +
          (Get-FileHash (Join-Path $FrontendDir "package.json") -Algorithm SHA256).Hash
if ((Test-Path $npmStamp) -and ((Get-Content $npmStamp -Raw).Trim() -eq $npmKey)) {
  Write-Host "  node_modules matches package-lock.json, skipping install." -ForegroundColor Gray
} else {
  npm ci
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: npm ci failed (exit $LASTEXITCODE). If package.json changed, run 'npm install' in frontend/ and commit package-lock.json." -ForegroundColor Red
    exit 1
  }
  $npmKey | Out-File $npmStamp -Encoding ascii
}

Write-Host "`n[2/2] Type-checking and building React app (Vite production)..." -ForegroundColor Cyan
npm run build
if ($LASTEXITCODE -ne 0) {
  Write-Host "  ERROR: build failed (exit $LASTEXITCODE) -- tsc -b or vite build" -ForegroundColor Red
  exit 1
}

$dist = Join-Path $FrontendDir "dist"
if (Test-Path $dist) {
  $sizeMB = [math]::Round((Get-ChildItem $dist -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
  Write-Host "`n  Frontend build complete: $dist ($sizeMB MB)" -ForegroundColor Green
} else {
  Write-Host "`n  Build failed - dist/ not found" -ForegroundColor Red
  exit 1
}

# Copy CHANGELOG.md into dist so the WelcomePanel can fetch it at /docs/CHANGELOG.md.
# The static file server in main.js serves everything from frontend_dist/, so the file
# must be present in dist/ at build time - it is not in the Vite source tree.
$changelogSrc = Join-Path $PSScriptRoot "..\..\docs\CHANGELOG.md"
if (Test-Path $changelogSrc) {
  $changelogDst = Join-Path $dist "docs"
  New-Item $changelogDst -ItemType Directory -Force | Out-Null
  Copy-Item $changelogSrc (Join-Path $changelogDst "CHANGELOG.md") -Force
  Write-Host "  Copied CHANGELOG.md -> dist/docs/CHANGELOG.md" -ForegroundColor Green
} else {
  # The What's New modal shows "Release notes are not available" when the file is absent.
  Write-Host "  docs/CHANGELOG.md not found - building without release notes." -ForegroundColor Yellow
}
