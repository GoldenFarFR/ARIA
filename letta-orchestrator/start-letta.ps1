# Démarre letta server (ou signale s'il tourne déjà sur :8283)
param([switch]$Force)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$env:LETTA_DIR = Join-Path $Here ".letta"
if (-not $env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT = (Resolve-Path (Join-Path $Here "..")).Path }

function Test-LettaUp {
    try {
        Invoke-WebRequest -Uri "http://localhost:8283/v1/agents/" -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Stop-LettaOnPort {
    $pids = @(Get-NetTCPConnection -LocalPort 8283 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($pid in $pids) {
        if ($pid -and $pid -ne 0) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
}

& (Join-Path $Here "sync-letta-env.ps1")

if (Test-LettaUp) {
    if (-not $Force) {
        Write-Host "Letta déjà actif → http://localhost:8283" -ForegroundColor Green
        exit 0
    }
    Write-Host "Redémarrage Letta (-Force)..." -ForegroundColor Yellow
    Stop-LettaOnPort
}

$letta = Join-Path $Here "venv\Scripts\letta.exe"
if (-not (Test-Path $letta)) { throw "venv/letta absent — lance .\install.ps1" }

Write-Host "Letta server → http://localhost:8283 (LETTA_DIR=$env:LETTA_DIR)" -ForegroundColor Cyan
& $letta server --port 8283