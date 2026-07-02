# ARIA Letta v2.4 — intégration profil PowerShell + pont Cursor
# Dot-source depuis Microsoft.PowerShell_profile.ps1 (via link-aria-profile.ps1)

$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path $PSScriptRoot -Parent) "aria-paths.ps1")

$script:AriaLettaDir = $script:AriaLettaRoot
$script:AriaLettaPy = Join-Path $script:AriaLettaDir "venv\Scripts\python.exe"
$script:AriaLettaOrchestrate = Join-Path $script:AriaLettaDir "orchestrate.ps1"
$script:AriaLettaStart = Join-Path $script:AriaLettaDir "start-letta.ps1"
$script:AriaLettaSyncEnv = Join-Path $script:AriaLettaDir "sync-letta-env.ps1"

function Import-AriaVaultEnv {
    $vault = Join-Path $env:LOCALAPPDATA "GoldenFar\vault"
    foreach ($name in @("local.env", "production.env")) {
        $path = Join-Path $vault $name
        if (-not (Test-Path $path)) { continue }
        Get-Content $path | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
                $k = $Matches[1]; $v = $Matches[2].Trim()
                if ($v -match '^"(.*)"$') { $v = $Matches[1] }
                if ($v) { Set-Item -Path "env:$k" -Value $v }
            }
        }
    }
    if (-not $env:XAI_API_KEY) {
        foreach ($src in @("GROK_API_KEY", "IMAGE_API_KEY")) {
            $candidate = [Environment]::GetEnvironmentVariable($src, "User")
            if (-not $candidate) { $candidate = [Environment]::GetEnvironmentVariable($src, "Process") }
            if ($candidate) { $env:XAI_API_KEY = $candidate; break }
        }
    }
    if (-not $env:ARIA_REPO_ROOT) { $env:ARIA_REPO_ROOT = $script:AriaRepoRoot }
    $env:LETTA_DIR = Join-Path $script:AriaLettaDir ".letta"
}

function Test-AriaLettaUp {
    try {
        Invoke-WebRequest -Uri "http://localhost:8283/v1/agents/" -UseBasicParsing -TimeoutSec 3 | Out-Null
        return $true
    } catch { return $false }
}

function Ensure-AriaLettaServer {
    if (Test-AriaLettaUp) { return $true }
    if (-not (Test-Path $script:AriaLettaStart)) { return $false }
    & $script:AriaLettaStart | Out-Null
    Start-Sleep -Seconds 2
    return (Test-AriaLettaUp)
}

function Invoke-AriaLetta {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [string]$Message,
        [ValidateSet("simple", "moyen", "complexe")]
        [string]$Niveau
    )

    if (-not (Test-Path $script:AriaLettaOrchestrate)) {
        Write-Host "Letta absent — lance letta-orchestrator\install.ps1" -ForegroundColor Red
        return
    }
    if (-not (Test-Path $script:AriaLettaPy)) {
        Write-Host "venv Letta absent — lance install.ps1" -ForegroundColor Red
        return
    }

    Import-AriaVaultEnv
    if (Test-Path $script:AriaLettaSyncEnv) { & $script:AriaLettaSyncEnv | Out-Null }

    if (-not (Ensure-AriaLettaServer)) {
        Write-Host "Letta injoignable (:8283). Lance : .\start-letta.ps1" -ForegroundColor Red
        return
    }

    Write-Host "`n[Letta] Routage multi-agents ARIA..." -ForegroundColor Magenta
    $params = @{ Message = $Message }
    if ($Niveau) { $params.Niveau = $Niveau }
    & $script:AriaLettaOrchestrate @params
}

# Remplace l'orchestrateur agent legacy (tool-calling PS) par Letta mémoire
function Invoke-AriaAgent {
    param([string]$TaskPrompt)
    if ($env:ARIA_AGENT_LEGACY -eq "1") {
        $legacy = Join-Path $script:AriaRepoRoot "scripts\PowerShell\aria_orchestrator.ps1"
        if (Test-Path $legacy) {
            $mem = Join-Path $script:AriaRepoRoot "memory"
            & $legacy -Model "qwen2.5:14b" -TaskPrompt $TaskPrompt -MemoryPath $mem
            return
        }
    }
    Invoke-AriaLetta -Message $TaskPrompt
}

Set-Alias -Name aria-letta -Value Invoke-AriaLetta -Force -Scope Global

Write-Host "ARIA Letta v2.4 charge (aria-letta / Invoke-AriaLetta)" -ForegroundColor DarkCyan