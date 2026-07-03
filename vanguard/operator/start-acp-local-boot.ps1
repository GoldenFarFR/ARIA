# Wrapper boot silencieux — appelé par la tâche planifiée Windows (pas de console)
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$MonorepoRoot = Split-Path -Parent (Split-Path -Parent $Root)
$LogDir = Join-Path $env:LOCALAPPDATA "GoldenFar"
$LogFile = Join-Path $LogDir "aria-boot.log"

function Write-BootLog([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') — $Message"
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Write-BootLog "=== boot ARIA local ==="

if (-not $env:ARIA_REPO_ROOT) {
    $env:ARIA_REPO_ROOT = $MonorepoRoot
}
$env:ARIA_RUNTIME = "local"

function Wait-Port([string]$HostName, [int]$Port, [int]$TimeoutSec = 120) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $iar = $tcp.BeginConnect($HostName, $Port, $null, $null)
            if ($iar.AsyncWaitHandle.WaitOne(2000, $false) -and $tcp.Connected) {
                $tcp.Close()
                return $true
            }
            $tcp.Close()
        } catch { }
        Start-Sleep -Seconds 3
    }
    return $false
}

# Déjà actif ?
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3 | Out-Null
    Write-BootLog "API :8000 déjà UP — skip"
    exit 0
} catch { }

Write-BootLog "Attente Ollama :11434 (max 120s)…"
if (-not (Wait-Port "127.0.0.1" 11434 120)) {
    Write-BootLog "WARN Ollama absent — démarrage bot quand même"
}

try {
    & (Join-Path $Root "start-acp-local.ps1") *>&1 | ForEach-Object { Write-BootLog $_ }
    Start-Sleep -Seconds 5
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 20
    Write-BootLog "Health OK acp=$($h.aria_acp.provider_enabled)"
    exit 0
} catch {
    Write-BootLog "ERREUR boot: $($_.Exception.Message)"
    exit 1
}