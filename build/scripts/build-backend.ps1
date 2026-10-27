<#
.SYNOPSIS
  Package the EVA Python backend into a distributable folder using PyInstaller.
  Output: EVA/backend/backend-dist/eva-backend/

.DESCRIPTION
  Builds from the hash-locked dependency files, never from a pre-existing
  environment:
    backend/requirements.lock        runtime dependencies (sha256-pinned)
    backend/requirements-build.lock  PyInstaller and its dependencies
  A disposable Python 3.11 virtual environment is created from those two files
  with uv, cached by the hash of the lock files, and checked for drift before
  PyInstaller runs.

.NOTES
  Run from any directory - all paths are resolved relative to this script.
  Always use Tee-Object when calling from a background task or CI runner:
    powershell -File build-backend.ps1 2>&1 | Tee-Object -FilePath build-backend.log
  PyInstaller takes 10-20 min with no visible output unless streamed via Tee-Object.

  Prerequisites:
    - uv (https://docs.astral.sh/uv/ ; winget install astral-sh.uv). uv downloads
      Python 3.11 itself if it is not installed.
    - Internet access (first run: packages, ONNX model; every run: lock freshness check
      unless -SkipLockCheck)
#>
param(
  [string]$BackendDir   = "$PSScriptRoot\..\..\backend",
  [string]$OutputDir    = "$PSScriptRoot\..\..\backend\backend-dist",
  [string]$OnnxDir      = "$PSScriptRoot\..\..\backend\onnx_model",
  [string]$TikDir       = "$PSScriptRoot\..\..\backend\tiktoken_cache",
  [string]$BuildEnvRoot = "$PSScriptRoot\..\..\backend\build-tmp\envs",
  [string]$PythonVersion = "3.11",
  [switch]$SkipLockCheck,   # skip the "lock files are up to date" check (needs network)
  [switch]$RebuildEnv,      # discard the cached build environment and recreate it
  [switch]$SkipSmokeTest    # skip running the packaged exe after the build (not recommended)
)

# Continue globally: PS5.1 wraps native-command stderr as NativeCommandError.
# With Stop that kills the script on any uv/python warning line even when exit
# code is 0. Use Continue here and check $LASTEXITCODE explicitly after each
# critical native command instead.
$ErrorActionPreference = 'Continue'

$BackendDir   = Resolve-Path $BackendDir
$OutputDir    = [IO.Path]::GetFullPath($OutputDir)
$OnnxDir      = [IO.Path]::GetFullPath($OnnxDir)
$TikDir       = [IO.Path]::GetFullPath($TikDir)
$BuildEnvRoot = [IO.Path]::GetFullPath($BuildEnvRoot)

$runtimeLock = Join-Path $BackendDir "requirements.lock"
$buildLock   = Join-Path $BackendDir "requirements-build.lock"
$checkScript = Join-Path $PSScriptRoot "check_build_env.py"

Write-Host "`n==============================" -ForegroundColor Cyan
Write-Host "  EVA Backend Build (PyInstaller)" -ForegroundColor Cyan
Write-Host "==============================" -ForegroundColor Cyan
Write-Host "  Backend source : $BackendDir" -ForegroundColor Gray
Write-Host "  Output         : $OutputDir" -ForegroundColor Gray

# ── Step 1: Locate uv and the lock files ─────────────────────────────────────
Write-Host "`n[1/7] Locating uv and lock files..." -ForegroundColor Cyan

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
  Write-Host "  ERROR: uv not found on PATH." -ForegroundColor Red
  Write-Host "  Install it:  winget install astral-sh.uv   (or see https://docs.astral.sh/uv/)" -ForegroundColor Yellow
  exit 1
}
$uv = $uvCmd.Source
Write-Host "  uv: $(& $uv --version)" -ForegroundColor Gray

foreach ($lock in @($runtimeLock, $buildLock)) {
  if (-not (Test-Path $lock)) {
    Write-Host "  ERROR: missing lock file: $lock" -ForegroundColor Red
    Write-Host "  Regenerate it with the command in the header of its .in / requirements.txt file." -ForegroundColor Yellow
    exit 1
  }
}
Write-Host "  Locks: requirements.lock, requirements-build.lock" -ForegroundColor Gray

# ── Step 2: Lock freshness check ─────────────────────────────────────────────
# Re-resolves requirements.txt / requirements-build.in against a COPY of the
# committed lock (uv keeps existing pins), and fails if the result differs, i.e.
# someone edited the direct requirements without re-locking.
Write-Host "`n[2/7] Checking the lock files are up to date..." -ForegroundColor Cyan

function Get-LockBody([string]$Path) {
  (Get-Content $Path) | Where-Object { $_.Trim() -ne '' -and $_ -notmatch '^\s*#' }
}

if ($SkipLockCheck) {
  Write-Host "  SKIPPED (-SkipLockCheck)." -ForegroundColor Yellow
} else {
  $tmpDir = Join-Path ([IO.Path]::GetTempPath()) ("eva-lockcheck-" + [Guid]::NewGuid().ToString('N'))
  New-Item $tmpDir -ItemType Directory -Force | Out-Null
  Push-Location $BackendDir
  try {
    $checks = @(
      @{ Name = 'requirements.lock';       In = 'requirements.txt';      Extra = @() },
      @{ Name = 'requirements-build.lock'; In = 'requirements-build.in'; Extra = @('-c', 'requirements.lock') }
    )
    foreach ($c in $checks) {
      $copy = Join-Path $tmpDir $c.Name
      Copy-Item (Join-Path $BackendDir $c.Name) $copy
      & $uv pip compile $c.In -o $copy --universal --python-version $PythonVersion --generate-hashes -q @($c.Extra)
      if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: could not re-resolve $($c.In) (exit $LASTEXITCODE). Offline? Use -SkipLockCheck." -ForegroundColor Red
        exit 1
      }
      $old = (Get-LockBody (Join-Path $BackendDir $c.Name)) -join "`n"
      $new = (Get-LockBody $copy) -join "`n"
      if ($old -ne $new) {
        Write-Host "  ERROR: $($c.Name) is out of date with $($c.In)." -ForegroundColor Red
        Write-Host "  Re-run the uv pip compile command from the header of $($c.In), review, and commit." -ForegroundColor Yellow
        exit 1
      }
      Write-Host "  $($c.Name) is current." -ForegroundColor Gray
    }
  } finally {
    Pop-Location
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
  }
}

# ── Step 3: Build environment (disposable, cached by lock hash) ──────────────
Write-Host "`n[3/7] Preparing the build environment (Python $PythonVersion)..." -ForegroundColor Cyan

$hashInput = "py$PythonVersion|" + (Get-FileHash $runtimeLock -Algorithm SHA256).Hash + "|" + (Get-FileHash $buildLock -Algorithm SHA256).Hash
$sha = [System.Security.Cryptography.SHA256]::Create()
$envKey = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($hashInput))[0..5] | ForEach-Object { $_.ToString('x2') })
$envDir = Join-Path $BuildEnvRoot "py$($PythonVersion.Replace('.', ''))-$envKey"
$python = Join-Path $envDir "Scripts\python.exe"
$marker = Join-Path $envDir ".eva-env-ready"

if ($RebuildEnv -and (Test-Path $envDir)) {
  Write-Host "  -RebuildEnv: removing $envDir" -ForegroundColor Yellow
  Remove-Item $envDir -Recurse -Force
}

if ((Test-Path $marker) -and (Test-Path $python)) {
  Write-Host "  Reusing cached environment: $envDir" -ForegroundColor Gray
} else {
  if (Test-Path $envDir) { Remove-Item $envDir -Recurse -Force }
  New-Item $BuildEnvRoot -ItemType Directory -Force | Out-Null

  Write-Host "  Creating environment: $envDir" -ForegroundColor Yellow
  # only-managed: always use uv's own CPython build, never a system or conda
  # Python, so the interpreter PyInstaller bundles does not depend on the machine.
  & $uv venv $envDir --python $PythonVersion --python-preference only-managed
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: uv venv failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
  }

  Write-Host "  Installing locked dependencies (hash-verified)..." -ForegroundColor Yellow
  & $uv pip sync --python $python --require-hashes $runtimeLock $buildLock
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: uv pip sync failed (exit $LASTEXITCODE)" -ForegroundColor Red
    Remove-Item $envDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
  }

  # Drift check before the environment is trusted and cached.
  & $python $checkScript $runtimeLock $buildLock
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: the build environment does not match the lock files." -ForegroundColor Red
    Remove-Item $envDir -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
  }

  "ready" | Out-File $marker -Encoding ascii

  # Drop environments built from older lock files (each is ~1 GB).
  Get-ChildItem $BuildEnvRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -ne $envDir } |
    ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "  Python: $python" -ForegroundColor Gray
& $python --version

# A cached environment is still re-verified: cheap, and catches a tampered cache.
& $python $checkScript $runtimeLock $buildLock
if ($LASTEXITCODE -ne 0) {
  Write-Host "  ERROR: build environment check failed. Re-run with -RebuildEnv." -ForegroundColor Red
  exit 1
}

# ── Step 4: Stage ONNX model + tiktoken cache ────────────────────────────────
Write-Host "`n[4/7] Staging ONNX model and tiktoken cache..." -ForegroundColor Cyan

# ONNX model (86 MB - cached after first download, skipped on repeat builds).
# The download is pinned to one Hugging Face commit and both files are checked
# against SHA-256 values, so a changed or tampered upstream cannot silently
# change the shipped product. To move to a newer model: change the revision and
# both hashes together, and review the resulting behaviour.
$ModelRepo     = 'sentence-transformers/all-MiniLM-L6-v2'
$ModelRevision = '1110a243fdf4706b3f48f1d95db1a4f5529b4d41'
$ModelFiles = @(
  @{ Remote = 'onnx/model.onnx'; Local = 'model.onnx';     Sha256 = '6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452' },
  @{ Remote = 'tokenizer.json';  Local = 'tokenizer.json'; Sha256 = 'be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037' }
)

function Test-ModelFiles {
  foreach ($f in $ModelFiles) {
    $p = Join-Path $OnnxDir $f.Local
    if (-not (Test-Path $p)) { return $false }
    if ((Get-FileHash $p -Algorithm SHA256).Hash.ToLower() -ne $f.Sha256) { return $false }
  }
  return $true
}

if (Test-ModelFiles) {
  $modelMB = [math]::Round((Get-Item "$OnnxDir\model.onnx").Length / 1MB, 1)
  Write-Host "  ONNX model cached and verified ($modelMB MB, revision $($ModelRevision.Substring(0,8))) - skipping download." -ForegroundColor Gray
} else {
  if (Test-Path $OnnxDir) {
    Write-Host "  Cached ONNX files are missing or do not match the pinned hashes - re-downloading." -ForegroundColor Yellow
    Remove-Item $OnnxDir -Recurse -Force
  }
  Write-Host "  Downloading $ModelRepo @ $($ModelRevision.Substring(0,8)) from HuggingFace Hub..." -ForegroundColor Yellow
  New-Item $OnnxDir -ItemType Directory -Force | Out-Null
  $OnnxDirEsc = $OnnxDir.Replace('\', '\\')
  & $python -c @"
from huggingface_hub import hf_hub_download
import shutil, os
repo = '$ModelRepo'
rev  = '$ModelRevision'
dst  = r'$OnnxDirEsc'
shutil.copy(hf_hub_download(repo, 'onnx/model.onnx', revision=rev), os.path.join(dst, 'model.onnx'))
shutil.copy(hf_hub_download(repo, 'tokenizer.json',  revision=rev), os.path.join(dst, 'tokenizer.json'))
print('ONNX model downloaded.')
"@
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: ONNX model download failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
  }
  foreach ($f in $ModelFiles) {
    $actual = (Get-FileHash (Join-Path $OnnxDir $f.Local) -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $f.Sha256) {
      Write-Host "  ERROR: $($f.Local) checksum mismatch." -ForegroundColor Red
      Write-Host "    expected $($f.Sha256)" -ForegroundColor Red
      Write-Host "    actual   $actual" -ForegroundColor Red
      Remove-Item $OnnxDir -Recurse -Force
      exit 1
    }
  }
  Write-Host "  ONNX model verified against the pinned SHA-256 values." -ForegroundColor Green
}

# tiktoken BPE cache (a few MB - generated by letting tiktoken encode once).
# No pin needed here: tiktoken checks the SHA-256 of every encoding file it downloads
# and refuses a mismatch.
if (-not (Test-Path $TikDir) -or (Get-ChildItem $TikDir -ErrorAction SilentlyContinue).Count -eq 0) {
  Write-Host "  Generating tiktoken BPE cache..." -ForegroundColor Yellow
  New-Item $TikDir -ItemType Directory -Force | Out-Null
  $TikDirEsc = $TikDir.Replace('\', '\\')
  & $python -c @"
import os
os.environ['TIKTOKEN_CACHE_DIR'] = r'$TikDirEsc'
import tiktoken
tiktoken.get_encoding('cl100k_base').encode('warm up')
tiktoken.get_encoding('o200k_base').encode('warm up')
print('tiktoken cache ready.')
"@
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: tiktoken cache generation failed (exit $LASTEXITCODE)" -ForegroundColor Red
    exit 1
  }
} else {
  Write-Host "  tiktoken cache already present - skipping." -ForegroundColor Gray
}

$onnxMB = [math]::Round((Get-ChildItem $OnnxDir -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
$tikFiles = (Get-ChildItem $TikDir -ErrorAction SilentlyContinue).Count
Write-Host "  onnx_model: $onnxMB MB | tiktoken_cache: $tikFiles files" -ForegroundColor Green

# ── Step 5: Run PyInstaller via eva.spec ─────────────────────────────────────
Write-Host "`n[5/7] Running PyInstaller..." -ForegroundColor Cyan

if (Test-Path $OutputDir) { Remove-Item $OutputDir -Recurse -Force }

$buildTmp = [IO.Path]::GetFullPath("$PSScriptRoot\..\..\backend\build-tmp\pyinstaller")
$specFile = Join-Path $BackendDir "eva.spec"

New-Item $buildTmp -ItemType Directory -Force | Out-Null

Set-Location $BackendDir

& $python -m PyInstaller $specFile `
    --clean `
    -y `
    --distpath $OutputDir `
    --workpath $buildTmp

if ($LASTEXITCODE -ne 0) {
  Write-Host "`n  PyInstaller failed (exit $LASTEXITCODE)" -ForegroundColor Red
  exit 1
}

# ── Step 6: Post-build - copy tools/ into _internal/, verify the bundle ──────
# tools/default/*.py are loaded via importlib.import_module() at runtime.
# PyInstaller does not bundle dynamically-imported packages automatically.
Write-Host "`n[6/7] Post-build: copying tools/ into _internal/ and verifying..." -ForegroundColor Cyan

$internalDir = Join-Path $OutputDir "eva-backend\_internal"
$toolsDst    = Join-Path $internalDir "tools"
$toolsSrc    = Join-Path $BackendDir "tools"

if (Test-Path $toolsDst) { Remove-Item $toolsDst -Recurse -Force }
New-Item "$toolsDst\default" -ItemType Directory -Force | Out-Null

Copy-Item "$toolsSrc\default\*.py" "$toolsDst\default\" -Force
"" | Out-File "$toolsDst\__init__.py"         -Encoding ascii
"" | Out-File "$toolsDst\default\__init__.py" -Encoding ascii

$toolCount = (Get-ChildItem "$toolsDst\default\*.py").Count
Write-Host "  $toolCount tool files copied + __init__.py markers added." -ForegroundColor Gray

# Visual C++ runtime: PyInstaller copies msvcp140/vcruntime140 from the build Python
# into _internal, and those are loaded in preference to the system ones. uv's managed
# CPython ships the 2019 runtime (14.29), but onnxruntime needs 14.40 or newer: with the
# old copy the backend dies with no Python error the first time a document is embedded.
# Replace them with the build machine's newer runtime (redistributable app-local).
$vcMinimum = [version]'14.40'

# Candidate folders: System32 (the installed Redistributable) and any Visual Studio
# redistributable folders (VC\Redist\MSVC\<ver>\x64\Microsoft.VC14x.CRT), so a build
# machine whose System32 copy is old can still bundle a current one.
$vcFolders = @(Join-Path $env:SystemRoot "System32")
foreach ($vsRoot in @("${env:ProgramFiles}\Microsoft Visual Studio", "${env:ProgramFiles(x86)}\Microsoft Visual Studio")) {
  if (Test-Path $vsRoot) {
    $vcFolders += Get-ChildItem $vsRoot -Recurse -Directory -Filter "Microsoft.VC14*.CRT" -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match '\\x64\\' } | ForEach-Object { $_.FullName }
  }
}

foreach ($dll in 'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll') {
  $target = Join-Path $internalDir $dll
  if (-not (Test-Path $target)) { continue }
  $best = $null
  foreach ($folder in $vcFolders) {
    $candidate = Join-Path $folder $dll
    if (-not (Test-Path $candidate)) { continue }
    $v = [version](((Get-Item $candidate).VersionInfo.FileVersion -split ' ')[0])
    if (-not $best -or $v -gt $best.Version) { $best = @{ Path = $candidate; Version = $v } }
  }
  if (-not $best -or $best.Version -lt $vcMinimum) {
    Write-Host "  ERROR: no copy of $dll >= $vcMinimum found on this machine (best: $($best.Version))." -ForegroundColor Red
    Write-Host "  Install the latest 'Microsoft Visual C++ Redistributable' (x64) and re-run." -ForegroundColor Yellow
    exit 1
  }
  Copy-Item $best.Path $target -Force
  Write-Host "  $dll -> $($best.Version)  ($($best.Path))" -ForegroundColor Gray
}

# The packaged backend must not contain PyMuPDF (AGPL) and must contain pypdf.
# Pure-Python modules are packed inside the PyInstaller archive (PYZ), not left
# as folders in _internal, so check the archive's module list and the folders.
$pyzToc = Get-ChildItem $buildTmp -Recurse -Filter "PYZ-00.toc" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pyzToc) {
  Write-Host "  ERROR: PyInstaller module list (PYZ-00.toc) not found under $buildTmp." -ForegroundColor Red
  exit 1
}
$pyzText = Get-Content $pyzToc.FullName -Raw
$agplFolders = Get-ChildItem $internalDir -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '^(fitz|pymupdf)' }
if ($agplFolders -or $pyzText -match "'(fitz|pymupdf)") {
  Write-Host "  ERROR: PyMuPDF (AGPL) found in the bundle." -ForegroundColor Red
  exit 1
}
if ($pyzText -notmatch "'pypdf'") {
  Write-Host "  ERROR: pypdf is missing from the bundle (PDF extraction would fail)." -ForegroundColor Red
  exit 1
}
Write-Host "  Bundle check OK: pypdf present, no PyMuPDF." -ForegroundColor Gray

# ── Step 7: Smoke test the packaged exe ──────────────────────────────────────
# Runs the real eva-backend.exe against a throw-away database on port 8000 and
# checks tools, agents, skills, MCPs, a PDF through ingest + search, a locked PDF.
Write-Host "`n[7/7] Smoke-testing the packaged backend..." -ForegroundColor Cyan
if ($SkipSmokeTest) {
  Write-Host "  SKIPPED (-SkipSmokeTest)." -ForegroundColor Yellow
} else {
  $smokeExe = Join-Path $OutputDir "eva-backend\eva-backend.exe"
  & $python (Join-Path $BackendDir "scripts\smoke_test_packaged_backend.py") $smokeExe $BackendDir
  if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: smoke test of the packaged backend failed (exit $LASTEXITCODE)." -ForegroundColor Red
    exit 1
  }
}

# ── Done ──────────────────────────────────────────────────────────────────────
$binaryPath = Join-Path $OutputDir "eva-backend\eva-backend.exe"
if (Test-Path $binaryPath) {
  $sizeMB = [math]::Round((Get-Item $binaryPath).Length / 1MB, 1)
  $bundleMB = [math]::Round((Get-ChildItem (Join-Path $OutputDir "eva-backend") -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
  Write-Host "`n  Backend build complete." -ForegroundColor Green
  Write-Host "  Binary : $binaryPath ($sizeMB MB)" -ForegroundColor Green
  Write-Host "  Bundle : $bundleMB MB total" -ForegroundColor Green
  Write-Host "  Output : $OutputDir\eva-backend\" -ForegroundColor Green
} else {
  Write-Host "`n  Build failed - binary not found at $binaryPath" -ForegroundColor Red
  exit 1
}
