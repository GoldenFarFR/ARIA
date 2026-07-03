# Rapport tokens LLM ARIA — SSOT data/llm-usage/YYYY-MM.jsonl
param([string]$Month = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path (Split-Path -Parent $Root) "backend"
$Core = Join-Path (Split-Path -Parent (Split-Path -Parent $Root)) "packages\aria-core"

if (-not $env:DATA_DIR) {
    $env:DATA_DIR = Join-Path $Backend "data"
}
if (-not $env:ARIA_REPO_ROOT) {
    $env:ARIA_REPO_ROOT = Split-Path -Parent (Split-Path -Parent $Root)
}

$py = Join-Path $Backend "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
}
if (-not $py) { throw "python introuvable" }

$args = @((Join-Path $Core "scripts\llm_usage_report.py"))
if ($Month) { $args += @("--month", $Month) }
& $py @args