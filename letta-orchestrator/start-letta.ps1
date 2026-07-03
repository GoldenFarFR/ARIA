# Démarre letta server (ou signale s'il tourne déjà sur :8283)
# Par défaut : arrière-plan (évite flood logs ADE). -Foreground pour debug.
param([switch]$Force, [switch]$Foreground)

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
    foreach ($procId in $pids) {
        if ($procId -and $procId -ne 0) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 2
}

& (Join-Path $Here "sync-letta-env.ps1")

function Import-LettaDotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $k = $Matches[1]; $v = $Matches[2].Trim()
            if ($v -match '^"(.*)"$') { $v = $Matches[1] }
            if ($v) { Set-Item -Path "env:$k" -Value $v }
        }
    }
}
Import-LettaDotEnv (Join-Path $Here ".env")
if (-not $env:GROQ_API_KEY -and $env:groq_api_key) { $env:GROQ_API_KEY = $env:groq_api_key }
if (-not $env:OLLAMA_BASE_URL -and $env:ollama_base_url) { $env:OLLAMA_BASE_URL = $env:ollama_base_url }

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
Write-Host "Tip : ferme l'onglet ADE (app.letta.com/development-servers) si logs en boucle." -ForegroundColor DarkGray

if ($Foreground) {
    & $letta server --port 8283
    exit $LASTEXITCODE
}

$logDir = Join-Path $Here "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logOut = Join-Path $logDir "letta-server.out.log"
$logErr = Join-Path $logDir "letta-server.err.log"

$lettaEnv = @{
    LETTA_DIR = $env:LETTA_DIR
    ARIA_REPO_ROOT = $env:ARIA_REPO_ROOT
}
if ($env:GROQ_API_KEY) { $lettaEnv.GROQ_API_KEY = $env:GROQ_API_KEY }
if ($env:OLLAMA_BASE_URL) { $lettaEnv.OLLAMA_BASE_URL = $env:OLLAMA_BASE_URL }
if ($env:ANTHROPIC_API_KEY) { $lettaEnv.ANTHROPIC_API_KEY = $env:ANTHROPIC_API_KEY }

Start-Process -FilePath $letta -ArgumentList "server", "--port", "8283" `
    -WorkingDirectory $Here -WindowStyle Hidden `
    -RedirectStandardOutput $logOut -RedirectStandardError $logErr `
    -Environment $lettaEnv

$deadline = (Get-Date).AddMinutes(2)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if (Test-LettaUp) {
        Write-Host "Letta OK (arrière-plan, logs: $logDir)" -ForegroundColor Green
        exit 0
    }
}
throw "Letta non joignable après 2 min — voir $logOut et $logErr"