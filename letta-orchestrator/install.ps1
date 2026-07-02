# ARIA v2.4 — Installation Letta multi-agents (monorepo GoldenFar)
# Usage : .\install.ps1 [-SkipOllama] [-SkipLettaServer]
param(
    [switch]$SkipOllama,
    [switch]$SkipLettaServer
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$RepoRoot = if ($env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT } else { (Resolve-Path (Join-Path $Here "..")).Path }
$VaultDir = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"

Write-Host "=== ARIA Letta v2.4 — install ===" -ForegroundColor Cyan
Write-Host "Repo : $RepoRoot"
Write-Host "Letta : $Here"

$env:LETTA_DIR = Join-Path $Here ".letta"
if (-not (Test-Path $env:LETTA_DIR)) { New-Item -ItemType Directory -Path $env:LETTA_DIR -Force | Out-Null }
[Environment]::SetEnvironmentVariable("LETTA_DIR", $env:LETTA_DIR, "User")

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) { return }
    Get-Content $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $k = $Matches[1]; $v = $Matches[2].Trim()
            if ($v -match '^"(.*)"$') { $v = $Matches[1] }
            if (-not [string]::IsNullOrWhiteSpace($v)) { Set-Item -Path "env:$k" -Value $v }
        }
    }
}

# Coffre GoldenFar + profil utilisateur
Import-DotEnv (Join-Path $VaultDir "local.env")
Import-DotEnv (Join-Path $VaultDir "production.env")

# Pont clés Letta ↔ écosystème ARIA
if (-not $env:XAI_API_KEY) {
    foreach ($src in @("GROK_API_KEY", "IMAGE_API_KEY", "LLM_API_KEY")) {
        $candidate = [Environment]::GetEnvironmentVariable($src, "Process")
        if (-not $candidate) { $candidate = [Environment]::GetEnvironmentVariable($src, "User") }
        if ($candidate) { $env:XAI_API_KEY = $candidate; break }
    }
}
if (-not $env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL = "http://127.0.0.1:11434" }
if (-not $env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT = $RepoRoot }

# Persistance user (sans écraser si déjà défini)
foreach ($pair in @(
    @{ Key = "XAI_API_KEY"; Src = @("XAI_API_KEY", "GROK_API_KEY") },
    @{ Key = "OLLAMA_BASE_URL"; Src = @("OLLAMA_BASE_URL") },
    @{ Key = "ARIA_REPO_ROOT"; Src = @("ARIA_REPO_ROOT") }
)) {
    $val = $null
    foreach ($s in $pair.Src) {
        $candidate = [Environment]::GetEnvironmentVariable($s, "Process")
        if ($candidate) { $val = $candidate; break }
    }
    if ($val -and -not [Environment]::GetEnvironmentVariable($pair.Key, "User")) {
        [Environment]::SetEnvironmentVariable($pair.Key, $val, "User")
        Write-Host "[env] $($pair.Key) enregistré (User)" -ForegroundColor DarkGray
    }
}

# venv Python — Letta requiert 3.11/3.12 (numpy<2 sans wheel 3.14)
$venv = Join-Path $Here "venv"
$pyLauncher = $null
foreach ($candidate in @("py -3.12", "py -3.11", "python")) {
    try {
        $ver = Invoke-Expression "$candidate --version 2>&1"
        if ($ver -match "3\.(11|12)\.") { $pyLauncher = $candidate; break }
    } catch { }
}
if (-not $pyLauncher) {
    throw "Python 3.11 ou 3.12 requis. Lance : py install 3.12"
}
if (Test-Path $venv) {
    $venvVer = & (Join-Path $venv "Scripts\python.exe") --version 2>&1
    if ($venvVer -notmatch "3\.(11|12)\.") {
        Write-Host "Recréation venv (version incompatible)..." -ForegroundColor Yellow
        Remove-Item $venv -Recurse -Force
    }
}
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Création venv ($pyLauncher)..." -ForegroundColor Yellow
    Invoke-Expression "$pyLauncher -m venv `"$venv`""
}
$py = Join-Path $venv "Scripts\python.exe"
& $py -m pip install --upgrade pip -q
& $py -m pip install -r (Join-Path $Here "requirements.txt")
& $py -m pip install asyncpg -q  # optionnel si migration vers letta>=0.16

# Modèles Ollama
if (-not $SkipOllama) {
    $models = @("qwen2.5:14b", "nomic-embed-text")
    foreach ($m in $models) {
        Write-Host "Ollama pull $m ..." -ForegroundColor Yellow
        ollama pull $m
    }
}

# Letta server
$lettaUp = $false
try {
    $r = Invoke-WebRequest -Uri "http://localhost:8283/v1/health" -UseBasicParsing -TimeoutSec 3
    $lettaUp = $r.StatusCode -eq 200
} catch {
    try {
        $r2 = Invoke-WebRequest -Uri "http://localhost:8283/" -UseBasicParsing -TimeoutSec 3
        $lettaUp = $true
    } catch { $lettaUp = $false }
}

& (Join-Path $Here "sync-letta-env.ps1")

if (-not $lettaUp -and -not $SkipLettaServer) {
    Write-Host "Démarrage letta server (arrière-plan)..." -ForegroundColor Yellow
    $lettaExe = Join-Path $venv "Scripts\letta.exe"
    if (-not (Test-Path $lettaExe)) { $lettaExe = "letta" }
    Start-Process -FilePath $lettaExe -ArgumentList "server","--port","8283" -WorkingDirectory $Here -WindowStyle Hidden
    $deadline = (Get-Date).AddMinutes(2)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        try {
            Invoke-WebRequest -Uri "http://localhost:8283/" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $lettaUp = $true
            break
        } catch { }
    }
}

if (-not $lettaUp) {
    Write-Host "[ATTENTION] Letta non joignable sur :8283 — lance manuellement : letta server" -ForegroundColor Red
} else {
    Write-Host "Letta OK (:8283)" -ForegroundColor Green
    & $py (Join-Path $Here "create_agents.py")
}

Write-Host "`n=== Terminé ===" -ForegroundColor Green
Write-Host "Test : .\orchestrate.ps1 -Message 'Bonjour ARIA'"
Write-Host "Activer venv : .\venv\Scripts\Activate.ps1"