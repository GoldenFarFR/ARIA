# Wrapper — crée ARIA-Ouvrier + outils Letta
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
if (-not $env:ARIA_REPO_ROOT) {
    $env:ARIA_REPO_ROOT = (Resolve-Path (Join-Path $Here "..")).Path
}
& (Join-Path $Here "sync-letta-env.ps1")
$py = Join-Path $Here "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv absent — .\install.ps1" }
& $py (Join-Path $Here "setup_ouvrier.py")