# Démarre letta server depuis le venv local (SQLite par défaut en 0.6.x)
$Here = $PSScriptRoot
$env:LETTA_DIR = Join-Path $Here ".letta"
if (-not $env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT = (Resolve-Path (Join-Path $Here "..")).Path }
& (Join-Path $Here "sync-letta-env.ps1")
$letta = Join-Path $Here "venv\Scripts\letta.exe"
if (-not (Test-Path $letta)) { throw "venv/letta absent — lance .\install.ps1" }
Write-Host "Letta server → http://localhost:8283 (LETTA_DIR=$env:LETTA_DIR)" -ForegroundColor Cyan
& $letta server --port 8283