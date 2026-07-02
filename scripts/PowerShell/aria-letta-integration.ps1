# ARIA Letta v2.4 — intégration profil PowerShell + pont Cursor
# Dot-source depuis Microsoft.PowerShell_profile.ps1 (via link-aria-profile.ps1)

$ErrorActionPreference = "Stop"

. (Join-Path (Split-Path $PSScriptRoot -Parent) "aria-paths.ps1")

$script:AriaLettaDir = $script:AriaLettaRoot
$script:AriaLettaPy = Join-Path $script:AriaLettaDir "venv\Scripts\python.exe"
$script:AriaLettaOrchestrate = Join-Path $script:AriaLettaDir "orchestrate.ps1"
$script:AriaLettaStart = Join-Path $script:AriaLettaDir "start-letta.ps1"
$script:AriaLettaSyncEnv = Join-Path $script:AriaLettaDir "sync-letta-env.ps1"
$script:AriaLettaModels = Join-Path $script:AriaLettaDir "models_config.json"
$script:AriaLettaAgents = Join-Path $script:AriaLettaDir "agents_config.json"

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
    if (-not $env:DATA_DIR -and (Test-Path $script:AriaDataDir)) {
        $env:DATA_DIR = $script:AriaDataDir
    }
    foreach ($pair in @(
            @{ Key = "ARIA_VECTOR_MEMORY"; Value = "true" }
            @{ Key = "ARIA_MEMORY_ARBITRATOR"; Value = "true" }
            @{ Key = "ARIA_DDG_SEARCH_CACHE"; Value = "true" }
        )) {
        Set-Item -Path "env:$($pair.Key)" -Value $pair.Value
    }
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

function Get-AriaLettaStatus {
    Write-Host "`n═══ ARIA LETTA STATUS ═══" -ForegroundColor Cyan

    $checks = [ordered]@{
        venv = Test-Path $script:AriaLettaPy
        agents_config = Test-Path $script:AriaLettaAgents
        models_config = Test-Path $script:AriaLettaModels
        server = Test-AriaLettaUp
    }

    foreach ($k in $checks.Keys) {
        $ok = $checks[$k]
        $color = if ($ok) { "Green" } else { "Red" }
        $label = if ($ok) { "OK" } else { "KO" }
        Write-Host ("  {0,-16} {1}" -f $k, $label) -ForegroundColor $color
    }

    if ($checks.models_config) {
        try {
            $models = Get-Content $script:AriaLettaModels -Raw | ConvertFrom-Json
            Write-Host "`n  Modèles :" -ForegroundColor DarkGray
            foreach ($prop in $models.PSObject.Properties) {
                Write-Host ("    {0,-10} {1}" -f $prop.Name, $prop.Value) -ForegroundColor DarkCyan
            }
        } catch { }
    }

    if ($checks.server) {
        try {
            $agents = Invoke-RestMethod -Uri "http://localhost:8283/v1/agents/" -TimeoutSec 5
            $count = @($agents).Count
            Write-Host "`n  Agents Letta : $count" -ForegroundColor DarkGray
            foreach ($a in $agents) {
                $name = if ($a.name) { $a.name } else { $a.id }
                $llm = if ($a.llm) { $a.llm } else { "?" }
                Write-Host ("    - {0} ({1})" -f $name, $llm) -ForegroundColor DarkCyan
            }
        } catch {
            Write-Host "  Agents Letta : lecture impossible" -ForegroundColor Yellow
        }
    }

    Import-AriaVaultEnv
    $keys = @("XAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_BASE_URL")
    Write-Host "`n  Clés / services :" -ForegroundColor DarkGray
    foreach ($key in $keys) {
        $val = [Environment]::GetEnvironmentVariable($key, "Process")
        if (-not $val) { $val = [Environment]::GetEnvironmentVariable($key, "User") }
        $present = -not [string]::IsNullOrWhiteSpace($val)
        $color = if ($present) { "Green" } else { "DarkYellow" }
        Write-Host ("    {0,-20} {1}" -f $key, $(if ($present) { "présente" } else { "absente" })) -ForegroundColor $color
    }

    $readme = Join-Path $script:AriaLettaDir "README-letta.md"
    if (Test-Path $readme) {
        Write-Host "`n  Doc : $readme" -ForegroundColor DarkGray
    }
    Write-Host "══════════════════════════`n" -ForegroundColor Cyan
}

function Get-AriaShellPython {
    $candidates = @(
        (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
        (Join-Path $script:AriaLettaDir "venv\Scripts\python.exe")
    ) | Where-Object { $_ -and (Test-Path $_) }
    foreach ($py in $candidates) {
        try {
            $ok = & $py -c "import aria_core" 2>$null
            if ($LASTEXITCODE -eq 0) { return $py }
        } catch { }
    }
    return $null
}

function Invoke-AriaBrain {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [string]$Message
    )
    Import-AriaVaultEnv
    $py = Get-AriaShellPython
    $script = Join-Path $script:AriaCorePackage "scripts\shell_chat.py"
    if (-not $py) {
        Write-Host "aria-core introuvable — pip install -e packages/aria-core[dev,vector]" -ForegroundColor Red
        return
    }
    if (-not (Test-Path $script)) {
        Write-Host "shell_chat.py absent" -ForegroundColor Red
        return
    }
    $t0 = Get-Date
    & $py $script --message $Message
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Cerveau ARIA en echec (code $LASTEXITCODE)" -ForegroundColor Red
        return
    }
    $sec = ((Get-Date) - $t0).TotalSeconds
    Write-Host "[ARIA-Brain | ${sec:N1}s | vector + COLLEGUE]" -ForegroundColor DarkGray
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

    $params = @{ Message = $Message }
    if ($Niveau) { $params.Niveau = $Niveau }
    & $script:AriaLettaOrchestrate @params
}

function Start-AriaLetta {
    [CmdletBinding()]
    param([switch]$Force, [switch]$Foreground)
    if (-not (Test-Path $script:AriaLettaStart)) {
        Write-Host "start-letta.ps1 absent : $script:AriaLettaStart" -ForegroundColor Red
        return
    }
    & $script:AriaLettaStart @PSBoundParameters
}

function aria-letta {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)

    if ($Args.Count -eq 0) {
        Write-Host "Usage: start-letta [-Force] | aria-letta status | aria-letta `"message`"" -ForegroundColor Yellow
        return
    }
    if ($Args[0] -eq "status") {
        Get-AriaLettaStatus
        return
    }
    if ($Args[0] -eq "start") {
        $force = $Args -contains "-Force"
        $fg = $Args -contains "-Foreground"
        Start-AriaLetta -Force:$force -Foreground:$fg
        return
    }

    $msg = $Args -join " "
    Invoke-AriaLetta -Message $msg
}

Set-Alias -Name start-letta -Value Start-AriaLetta -Force -ErrorAction SilentlyContinue

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
    Invoke-AriaBrain -Message $TaskPrompt
}

Import-AriaVaultEnv
Write-Host "ARIA shell v2.5 (cerveau aria-core + vector + COLLEGUE) — /letta pour Letta seul" -ForegroundColor DarkCyan