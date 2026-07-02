# Demarrage ACP local — listener + sync env + bot API :8000
# Usage : cd %ARIA_REPO_ROOT%\vanguard\operator ; .\start-acp-local.ps1

param(
    [switch]$SkipBot,
    [switch]$SkipListener
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "..\backend"

Write-Host "=== ARIA ACP local ===" -ForegroundColor Cyan

if (-not $SkipListener) {
    & (Join-Path $Root "acp-events-listener.ps1") -Background -Mode legacy
}

& (Join-Path $Root "sync-local.ps1")

if ($SkipBot) {
    Write-Host "Bot non demarre (-SkipBot). Lance manuellement uvicorn sur :8000" -ForegroundColor Yellow
    exit 0
}

$pyCandidates = @(
    (Join-Path $Backend "venv\Scripts\python.exe"),
    (Join-Path $env:USERPROFILE "GitHub-Repos\ARIA\letta-orchestrator\venv\Scripts\python.exe"),
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
)
$py = $null
foreach ($candidate in $pyCandidates) {
    if (-not $candidate -or -not (Test-Path $candidate)) { continue }
    & $candidate -c "import uvicorn" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
}
if (-not $py) {
    Write-Host "uvicorn introuvable — pip install uvicorn dans le venv backend" -ForegroundColor Red
    exit 1
}

$logBot = Join-Path $env:LOCALAPPDATA "GoldenFar\aria-bot-local.log"
Write-Host "Demarrage API :8000 (log $logBot)..." -ForegroundColor Green
Start-Process -FilePath $py -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
) -WorkingDirectory $Backend -RedirectStandardOutput $logBot -RedirectStandardError $logBot `
    -WindowStyle Hidden

Start-Sleep -Seconds 3
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 8
    $acp = $health.aria_acp
    Write-Host "Health OK — acp cli=$($acp.cli_available) provider=$($acp.provider_enabled)" -ForegroundColor Green
} catch {
    Write-Host "Health pas encore OK — voir $logBot" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Commandes chat/Telegram : acp status | traiter jobs acp" -ForegroundColor Cyan
Write-Host "Arreter bot : Stop-Process -Name python -ErrorAction SilentlyContinue" -ForegroundColor DarkGray