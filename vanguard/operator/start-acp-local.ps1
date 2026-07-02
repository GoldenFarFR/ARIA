# Demarrage ACP local — listener + sync env + bot API :8000
# Usage : cd %ARIA_REPO_ROOT%\vanguard\operator ; .\start-acp-local.ps1

param(
    [switch]$SkipBot,
    [switch]$SkipListener
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VanguardRoot = Split-Path -Parent $Root
$MonorepoRoot = Split-Path -Parent $VanguardRoot
$Backend = Join-Path $VanguardRoot "backend"
$CorePackage = Join-Path $MonorepoRoot "packages\aria-core"

function Ensure-BackendPython {
    param([string]$PythonExe)
    if (-not (Test-Path $CorePackage)) {
        throw "aria-core introuvable : $CorePackage"
    }
    & $PythonExe -m pip install -q -e $CorePackage
    if ($LASTEXITCODE -ne 0) { throw "pip install aria-core a echoue" }
    $req = Join-Path $Backend "requirements.txt"
    if (Test-Path $req) {
        & $PythonExe -m pip install -q -r $req
        if ($LASTEXITCODE -ne 0) { throw "pip install backend requirements a echoue" }
    }
    $prevPath = $env:PYTHONPATH
    $env:PYTHONPATH = $Backend
    & $PythonExe -c "from app.main import app; print('import ok')"
    $ok = ($LASTEXITCODE -eq 0)
    $env:PYTHONPATH = $prevPath
    if (-not $ok) { throw "import app.main a echoue — lance build-local.ps1" }
}

Write-Host "=== ARIA ACP local ===" -ForegroundColor Cyan

$goldenFar = Join-Path $env:LOCALAPPDATA "GoldenFar"
New-Item -ItemType Directory -Path $goldenFar -Force | Out-Null

# Libere .env + port 8000 si un ancien bot tourne encore
try {
    $on8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $on8000) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "Arret process :8000 (pid $procId)" -ForegroundColor DarkYellow
    }
    Start-Sleep -Seconds 1
} catch { }

if (-not $SkipListener) {
    & (Join-Path $Root "acp-events-listener.ps1") -Background -Mode legacy
}

$envDst = Join-Path $Backend ".env"
try {
    & (Join-Path $Root "sync-local.ps1")
} catch {
    if ((Test-Path $envDst) -and (Select-String -Path $envDst -Pattern "ARIA_ACP_PROVIDER_ENABLED=true" -Quiet)) {
        Write-Host "sync-local skip — .env verrouille mais ACP deja configure" -ForegroundColor Yellow
    } else {
        throw
    }
}

if ($SkipBot) {
    Write-Host "Bot non demarre (-SkipBot). Lance manuellement uvicorn sur :8000" -ForegroundColor Yellow
    exit 0
}

$pyCandidates = @(
    (Join-Path $Backend "venv\Scripts\python.exe"),
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    (Join-Path $env:USERPROFILE "GitHub-Repos\ARIA\letta-orchestrator\venv\Scripts\python.exe")
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

Write-Host "Preparation backend (aria-core editable)..." -ForegroundColor DarkGray
Ensure-BackendPython -PythonExe $py

$logBot = Join-Path $goldenFar "aria-bot-local.log"
$logErr = Join-Path $goldenFar "aria-bot-local.err.log"
Write-Host "Demarrage API :8000 (log $logBot)..." -ForegroundColor Green
$procEnv = @{
    PYTHONPATH = $Backend
}
if ($env:ARIA_REPO_ROOT) { $procEnv["ARIA_REPO_ROOT"] = $env:ARIA_REPO_ROOT }
if ($env:DATA_DIR) { $procEnv["DATA_DIR"] = $env:DATA_DIR }
Start-Process -FilePath $py -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
) -WorkingDirectory $Backend -RedirectStandardOutput $logBot -RedirectStandardError $logErr `
    -WindowStyle Hidden -Environment $procEnv

Start-Sleep -Seconds 8
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 15
    $acp = $health.aria_acp
    Write-Host "Health OK — acp cli=$($acp.cli_available) provider=$($acp.provider_enabled)" -ForegroundColor Green
} catch {
    Write-Host "Health pas encore OK — voir $logBot" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Commandes chat/Telegram : acp status | traiter jobs acp" -ForegroundColor Cyan
Write-Host "Arreter bot : Stop-Process -Name python -ErrorAction SilentlyContinue" -ForegroundColor DarkGray