# Active la super-memoire locale : Chroma + ingest + verification LLM
# Usage: .\activate-vector-local.ps1 [-DataDir <path>]

param(
    [string]$DataDir = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "..\vanguard\backend\data")
)

$ErrorActionPreference = "Stop"
$core = Split-Path -Parent $PSScriptRoot
$resolvedData = (Resolve-Path $DataDir -ErrorAction SilentlyContinue)
if (-not $resolvedData) {
    $resolvedData = (Resolve-Path (Join-Path $core "..\..\vanguard\backend\data")).Path
} else {
    $resolvedData = $resolvedData.Path
}

$env:PYTHONPATH = Join-Path $core "src"
$env:DATA_DIR = $resolvedData
$env:ARIA_VECTOR_MEMORY = "true"
$env:ARIA_DDG_SEARCH_CACHE = "true"
$env:ARIA_MEMORY_ARBITRATOR = "true"

Write-Host "=== Activation memoire vectorielle locale ===" -ForegroundColor Cyan
Write-Host "DATA_DIR: $resolvedData"

python (Join-Path $PSScriptRoot "_activate_vector_local.py") --data-dir $resolvedData

Write-Host "activate-vector-local OK" -ForegroundColor Green