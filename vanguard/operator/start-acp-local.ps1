# Demarrage bot ARIA local — API :8000 (+ sync env + listener ACP optionnel)
# Usage : .\start-acp-local.ps1
# Cursor/Grok : toujours lancer CE fichier, jamais de gros bloc pwsh inline.

param(
    [switch]$SkipBot,
    [switch]$SkipListener,
    [switch]$TestChat
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path (Split-Path (Split-Path $Root -Parent) -Parent) "scripts\aria-paths.ps1")

$MonorepoRoot = $script:AriaRepoRoot
$Backend = Join-Path $script:AriaVanguardRoot "backend"
$CorePackage = $script:AriaCorePackage
$OpsRoot = $script:AriaOperatorRoot

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
    if (-not $ok) { throw "import app.main a echoue — lance build-local.ps1 (aria-ops operator)" }
}

Write-Host "=== ARIA local ($MonorepoRoot) ===" -ForegroundColor Cyan

$goldenFar = Join-Path $env:LOCALAPPDATA "GoldenFar"
New-Item -ItemType Directory -Path $goldenFar -Force | Out-Null

try {
    $on8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $on8000) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "Arret process :8000 (pid $procId)" -ForegroundColor DarkYellow
    }
    if ($on8000) { Start-Sleep -Seconds 2 }
} catch { }

if (-not $SkipListener) {
    $listener = Join-Path $OpsRoot "acp-events-listener.ps1"
    if (Test-Path $listener) {
        & $listener -Background -Mode legacy
    } else {
        Write-Host "Listener ACP absent ($listener) — -SkipListener implicite" -ForegroundColor Yellow
    }
}

$envDst = Join-Path $Backend ".env"
$syncLocal = Join-Path $OpsRoot "sync-local.ps1"
if (Test-Path $syncLocal) {
    try {
        & $syncLocal
    } catch {
        if ((Test-Path $envDst) -and (Select-String -Path $envDst -Pattern "ARIA_ACP_PROVIDER_ENABLED=true" -Quiet)) {
            Write-Host "sync-local skip — .env deja configure" -ForegroundColor Yellow
        } else {
            throw
        }
    }
} elseif (-not (Test-Path $envDst)) {
    Write-Host ".env manquant — lance setup.ps1 dans aria-ops/vanguard/operator" -ForegroundColor Red
    exit 1
}

if ($SkipBot) {
    Write-Host "Bot non demarre (-SkipBot)." -ForegroundColor Yellow
    exit 0
}

$pyCandidates = @(
    (Join-Path $Backend "venv\Scripts\python.exe"),
    (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    (Join-Path $env:USERPROFILE "GitHub-Repos\aria-ops\letta-orchestrator\venv\Scripts\python.exe")
)
$py = $null
foreach ($candidate in $pyCandidates) {
    if (-not $candidate -or -not (Test-Path $candidate)) { continue }
    & $candidate -c "import uvicorn" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = $candidate; break }
}
if (-not $py) {
    Write-Host "uvicorn introuvable — cree le venv backend puis pip install uvicorn" -ForegroundColor Red
    exit 1
}

Write-Host "Preparation backend (aria-core editable)..." -ForegroundColor DarkGray
Ensure-BackendPython -PythonExe $py

$logBot = Join-Path $goldenFar "aria-bot-local.log"
$logErr = Join-Path $goldenFar "aria-bot-local.err.log"
Write-Host "Demarrage API :8000 (log $logBot)..." -ForegroundColor Green

$procEnv = @{}
foreach ($entry in [System.Environment]::GetEnvironmentVariables("Process").GetEnumerator()) {
    $procEnv[$entry.Key] = [string]$entry.Value
}
$procEnv["PYTHONPATH"] = $Backend
$procEnv["ARIA_REPO_ROOT"] = $MonorepoRoot
if ($env:DATA_DIR) { $procEnv["DATA_DIR"] = $env:DATA_DIR }

$backendEnv = Join-Path $Backend ".env"
if (Test-Path $backendEnv) {
    . (Join-Path $OpsRoot "_render-common.ps1")
    $dotenv = Read-EnvFile -Path $backendEnv
    foreach ($key in $dotenv.Keys) {
        $procEnv[$key] = $dotenv[$key]
    }
}
if ($procEnv["LLM_PROVIDER"]) {
    Write-Host "LLM: $($procEnv['LLM_PROVIDER']) / $($procEnv['LLM_MODEL'])" -ForegroundColor DarkGray
}

# Minimized (pas Hidden) — moins de faux positifs Defender PowhidSubExec
Start-Process -FilePath $py -ArgumentList @(
    "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"
) -WorkingDirectory $Backend -RedirectStandardOutput $logBot -RedirectStandardError $logErr `
    -WindowStyle Minimized -Environment $procEnv

Start-Sleep -Seconds 8
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 15
    $acp = $health.aria_acp
    Write-Host "Health OK — acp cli=$($acp.cli_available) provider=$($acp.provider_enabled)" -ForegroundColor Green
} catch {
    Write-Host "Health pas encore OK — voir $logBot" -ForegroundColor Yellow
}

if ($TestChat) {
    & (Join-Path $Root "test-aria-chat.ps1")
}

Write-Host ""
Write-Host "Arreter bot : Get-NetTCPConnection -LocalPort 8000 | %% { Stop-Process -Id `$_.OwningProcess -Force }" -ForegroundColor DarkGray