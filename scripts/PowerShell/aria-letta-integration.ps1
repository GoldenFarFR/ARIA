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
    $prodOverlay = @(
        "LLM_PROVIDER", "LLM_MODEL", "VIRTUALS_API_KEY", "LLM_FALLBACK_API_KEY",
        "LLM_FALLBACK_PROVIDER", "LLM_FALLBACK_MODEL", "ARIA_SPARK_AGGRESSIVE",
        "ARIA_LLM_MODEL_DEVELOP", "ARIA_LLM_MODEL_STANDARD", "ARIA_LLM_MODEL_BRIEF",
        "ARIA_OUVRIER_CLOUD", "ARIA_OUVRIER_SKIP_GROQ_FALLBACK"
    )
    foreach ($name in @("local.env", "production.env")) {
        $path = Join-Path $vault $name
        if (-not (Test-Path $path)) { continue }
        Get-Content $path | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
                $k = $Matches[1]; $v = $Matches[2].Trim()
                if ($v -match '^"(.*)"$') { $v = $Matches[1] }
                if (-not $v) { return }
                if ($name -eq "local.env" -and $k -in $prodOverlay) { return }
                Set-Item -Path "env:$k" -Value $v
            }
        }
    }
    $prodPath = Join-Path $vault "production.env"
    if (Test-Path $prodPath) {
        Get-Content $prodPath | ForEach-Object {
            if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
                $k = $Matches[1]; $v = $Matches[2].Trim()
                if ($v -match '^"(.*)"$') { $v = $Matches[1] }
                if ($k -in $prodOverlay -and $v) { Set-Item -Path "env:$k" -Value $v }
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
    $groq = $env:GROQ_API_KEY
    if (-not $groq -or $groq.Length -lt 20) {
        foreach ($src in @("LLM_API_KEY", "GROQ_API_KEY")) {
            $candidate = $null
            foreach ($name in @("local.env", "production.env")) {
                $path = Join-Path $vault $name
                if (-not (Test-Path $path)) { continue }
                $line = Select-String -Path $path -Pattern "^\s*$src=" -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($line -and $line.Line -match '=\s*(.+)$') {
                    $candidate = $Matches[1].Trim().Trim('"')
                    if ($candidate.Length -ge 20) { break }
                }
            }
            if ($candidate -and $candidate.Length -ge 20) {
                $env:GROQ_API_KEY = $candidate
                break
            }
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

function Get-AriaKartGrokTokens {
    <#
    .SYNOPSIS
    Compteur tokens Grok Build / xAI — JSONL locale llm-usage, 0 appel API.
    #>
    Import-AriaVaultEnv
    if (-not $env:DATA_DIR -and $script:AriaDataDir -and (Test-Path $script:AriaDataDir)) {
        $env:DATA_DIR = $script:AriaDataDir
    }
    $report = Join-Path $script:AriaCorePackage "scripts\llm_usage_report.py"
    if (-not (Test-Path $report)) {
        return "[GROK BUILD] n/d (script absent)"
    }
    $pys = @(
        (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
        (Join-Path $script:AriaLettaDir "venv\Scripts\python.exe")
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
    foreach ($py in $pys) {
        try {
            $line = (& $py $report --grok 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $line -match '^grok\s') {
                return "[GROK BUILD] $line"
            }
        } catch { }
    }
    return "[GROK BUILD] n/d"
}

function Get-AriaKartPaidTokens {
    return Get-AriaKartGrokTokens
}

function Get-AriaKartCursorUsage {
    <#
    .SYNOPSIS
    Quota Cursor Pro — état local %LOCALAPPDATA%\GoldenFar\cursor-usage.json
    #>
    Import-AriaVaultEnv
    if (-not $env:DATA_DIR -and $script:AriaDataDir -and (Test-Path $script:AriaDataDir)) {
        $env:DATA_DIR = $script:AriaDataDir
    }
    $report = Join-Path $script:AriaCorePackage "scripts\llm_usage_report.py"
    if (-not (Test-Path $report)) {
        return "[CURSOR] n/d (script absent)"
    }
    $py = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    if (-not $py) { return "[CURSOR] n/d (python)" }
    try {
        $line = (& $py $report --cursor 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -eq 0 -and $line) {
            return "[CURSOR] $line"
        }
    } catch { }
    return "[CURSOR] n/d — /cursor-usage 4"
}

function Set-AriaCursorUsage {
    param(
        [double]$ComposerPct = -1,
        [double]$ApiPct = -1,
        [string]$Plan = ""
    )
    Import-AriaVaultEnv
    $report = Join-Path $script:AriaCorePackage "scripts\llm_usage_report.py"
    $py = (Get-Command python -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    if (-not ($py -and (Test-Path $report))) {
        Write-Host "cursor-usage: script absent" -ForegroundColor Red
        return
    }
    $args = @($report, "--set-cursor")
    if ($ComposerPct -ge 0) { $args += "composer_pct=$ComposerPct" }
    if ($ApiPct -ge 0) { $args += "api_pct=$ApiPct" }
    if ($Plan) { $args += "plan=$Plan" }
    $line = (& $py @args 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[CURSOR] $line" -ForegroundColor Green
    } else {
        Write-Host "cursor-usage: echec" -ForegroundColor Red
    }
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
    Write-Host "`n[Cerveau aria-core] vector + COLLEGUE + skills…" -ForegroundColor Cyan
    $t0 = Get-Date
    & $py $script --message $Message
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Cerveau ARIA en echec (code $LASTEXITCODE)" -ForegroundColor Red
        return
    }
    $sec = ((Get-Date) - $t0).TotalSeconds
    Write-Host "[ARIA-Brain | ${sec:N1}s | vector + COLLEGUE]" -ForegroundColor DarkGray
}

$script:AriaOuvrierOrchestrate = Join-Path $script:AriaLettaDir "orchestrate-ouvrier.ps1"
$script:AriaUnifiedOrchestrate = Join-Path $script:AriaLettaDir "orchestrate-unified.ps1"
$script:AriaOuvrierConfig = Join-Path $script:AriaLettaDir "ouvrier_config.json"

function Test-AriaOuvrierReady {
    return (Test-Path $script:AriaOuvrierOrchestrate) -and (Test-Path $script:AriaOuvrierConfig)
}

function Invoke-AriaOuvrier {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [string]$Message
    )

    if (-not (Test-Path $script:AriaOuvrierOrchestrate)) {
        Write-Host "Ouvrier absent — lance letta-orchestrator\setup-ouvrier.ps1" -ForegroundColor Red
        return
    }
    if (-not (Test-Path $script:AriaOuvrierConfig)) {
        Write-Host "ouvrier_config.json absent — .\setup-ouvrier.ps1" -ForegroundColor Red
        return
    }

    Import-AriaVaultEnv

    $ouvrierParams = @{ Message = $Message }
    if ($env:ARIA_OUVRIER_VERBOSE -eq "1") { $ouvrierParams.ShowTrace = $true }
    & $script:AriaOuvrierOrchestrate @ouvrierParams
}

function Test-AriaUnifiedReady {
    return (Test-Path $script:AriaUnifiedOrchestrate) -and (Test-Path $script:AriaOuvrierConfig)
}

function Invoke-AriaUnified {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)]
        [string]$Message
    )
    if (-not (Test-Path $script:AriaUnifiedOrchestrate)) {
        Write-Host "ARIA unifiee absente — fallback ouvrier" -ForegroundColor Yellow
        Invoke-AriaOuvrier -Message $Message
        return
    }
    if (-not (Test-Path $script:AriaOuvrierConfig)) {
        Write-Host "ouvrier_config.json absent — .\setup-ouvrier.ps1" -ForegroundColor Red
        return
    }
    Import-AriaVaultEnv
    $params = @{ Message = $Message }
    if ($env:ARIA_OUVRIER_VERBOSE -eq "1") { $params.ShowTrace = $true }
    & $script:AriaUnifiedOrchestrate @params
}

function Invoke-AriaKartDefault {
    param(
        [Parameter(Mandatory)][string]$Message,
        [string]$Intent = "OUVRIER"
    )
    switch ($Intent) {
        "GROK" { Invoke-Grok -Prompt $Message; return }
        "GROQ" { Invoke-Groq -Prompt $Message; return }
        "CHAT" { Invoke-Ollama -Prompt $Message; return }
        "OUVRIER" {
            if (Test-AriaOuvrierReady) {
                Invoke-AriaOuvrier -Message $Message
            } else {
                Invoke-AriaBrain -Message $Message
            }
            return
        }
        default {
            if (Test-AriaUnifiedReady) {
                Invoke-AriaUnified -Message $Message
            } elseif (Test-AriaOuvrierReady) {
                Invoke-AriaOuvrier -Message $Message
            } else {
                Write-Host "ARIA non configure — lance setup-ouvrier.ps1" -ForegroundColor Yellow
                Invoke-AriaBrain -Message $Message
            }
            return
        }
    }
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

function Invoke-AriaApplyLessons {
    [CmdletBinding()]
    param(
        [ValidateSet("list", "approved", "approve", "apply")]
        [string]$Action = "list",
        [int]$Index = 0
    )
    $script = Join-Path $script:AriaLettaDir "apply-pending-lessons.ps1"
    if (-not (Test-Path $script)) {
        Write-Host "apply-pending-lessons.ps1 absent — letta-orchestrator Sprint 4" -ForegroundColor Red
        return
    }
    switch ($Action) {
        "list" { & $script -List }
        "approved" { & $script -ApplyApproved }
        "approve" {
            if ($Index -lt 1) {
                Write-Host "Usage: /apply-lessons approve N" -ForegroundColor Yellow
                return
            }
            & $script -Approve $Index
        }
        "apply" {
            if ($Index -lt 1) {
                Write-Host "Usage: /apply-lessons apply N" -ForegroundColor Yellow
                return
            }
            & $script -Apply $Index
        }
    }
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
    Invoke-AriaOuvrier -Message $TaskPrompt
}

Import-AriaVaultEnv
Write-Host "ARIA shell v3.0 — Unifiee (cerveau+ouvrier+ACP) | /apply-lessons | /cerveau = brain seul" -ForegroundColor DarkCyan