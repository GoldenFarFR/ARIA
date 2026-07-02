# Fonctions partagees API Render - dot-source depuis les autres scripts

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_vault-common.ps1")

function Get-RenderApiKey {
    param([string]$Root = "")
    $ApiKeyFile = Get-RenderApiKeyPath -ScriptsRoot $Root
    if (-not (Test-Path $ApiKeyFile)) { return $null }
    return (Get-Content $ApiKeyFile -Raw).Trim()
}

function Get-RenderHeaders {
    param([string]$ApiKey)
    return @{
        Authorization  = "Bearer $ApiKey"
        Accept         = "application/json"
        "Content-Type" = "application/json"
    }
}

function Find-RenderServiceId {
    param(
        [hashtable]$Headers,
        [string]$ServiceName = "dexpulse"
    )
    $cursor = ""
    do {
        $uri = "https://api.render.com/v1/services?limit=100"
        if ($cursor) { $uri += "&cursor=$cursor" }
        $resp = Invoke-RestMethod -Uri $uri -Headers $Headers -Method Get
        foreach ($item in $resp) {
            if ($item.service.name -ieq $ServiceName) {
                return $item.service.id
            }
        }
        $cursor = if ($resp.Count -gt 0) { $resp[-1].cursor } else { "" }
    } while ($cursor)
    return $null
}

function Get-RenderEnvVars {
    param(
        [hashtable]$Headers,
        [string]$ServiceId
    )
    $vars = @{}
    $cursor = ""
    do {
        $uri = "https://api.render.com/v1/services/$ServiceId/env-vars?limit=100"
        if ($cursor) { $uri += "&cursor=$cursor" }
        $resp = Invoke-RestMethod -Uri $uri -Headers $Headers -Method Get
        foreach ($item in $resp) {
            $ev = $item.envVar
            if ($ev.key) { $vars[$ev.key] = $ev.value }
        }
        $cursor = if ($resp.Count -gt 0) { $resp[-1].cursor } else { "" }
    } while ($cursor)
    return $vars
}

function Read-EnvFile {
    param([string]$Path)
    $vars = [ordered]@{}
    if (-not (Test-Path $Path)) { return $vars }
    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $idx = $line.IndexOf("=")
        if ($idx -lt 1) { return }
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        $vars[$key] = $val
    }
    return $vars
}

function Write-EnvFile {
    param(
        [string]$Path,
        [string[]]$HeaderLines,
        [hashtable]$Vars,
        [string[]]$KeyOrder
    )
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($h in $HeaderLines) { $lines.Add($h) }
    foreach ($key in $KeyOrder) {
        if ($Vars.Contains($key)) {
            $lines.Add("$key=$($Vars[$key])")
        }
    }
    foreach ($key in $Vars.Keys) {
        if ($key -notin $KeyOrder) {
            $lines.Add("$key=$($Vars[$key])")
        }
    }
    Set-Content -Path $Path -Value $lines -Encoding UTF8
}

function Start-RenderServiceDeploy {
    param(
        [hashtable]$Headers,
        [string]$ServiceId
    )
    $resp = Invoke-RestMethod `
        -Uri "https://api.render.com/v1/services/$ServiceId/deploys" `
        -Method Post `
        -Headers $Headers `
        -Body '{}' `
        -ContentType 'application/json'
    return $resp.deploy
}

function Wait-RenderServiceDeploy {
    param(
        [hashtable]$Headers,
        [string]$ServiceId,
        [string]$DeployId = "",
        [int]$TimeoutSeconds = 300,
        [int]$PollSeconds = 10
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Seconds $PollSeconds
        $deploys = Invoke-RestMethod `
            -Uri "https://api.render.com/v1/services/$ServiceId/deploys?limit=5" `
            -Headers $Headers
        $latest = $null
        if ($DeployId) {
            foreach ($item in $deploys) {
                if ($item.deploy.id -eq $DeployId) {
                    $latest = $item.deploy
                    break
                }
            }
        }
        if (-not $latest -and $deploys.Count -gt 0) {
            $latest = $deploys[0].deploy
        }
        if (-not $latest) { continue }
        Write-Host "  deploy $($latest.status)..." -ForegroundColor DarkGray
        if ($latest.status -eq 'live') { return $latest }
        if ($latest.status -in @('build_failed', 'update_failed', 'canceled')) {
            throw "Deploy Render echoue: $($latest.status)"
        }
    } while ((Get-Date) -lt $deadline)
    throw "Timeout en attente du deploy Render (${TimeoutSeconds}s)"
}

function Get-RenderPipelineStatus {
    param(
        [hashtable]$Headers,
        [string]$ServiceId,
        [int]$EventLimit = 12
    )
    try {
        $events = Invoke-RestMethod `
            -Uri "https://api.render.com/v1/services/$ServiceId/events?limit=$EventLimit" `
            -Headers $Headers
        foreach ($item in $events) {
            $ev = $item.event
            if ($ev.type -eq "pipeline_minutes_exhausted") {
                return @{
                    exhausted = $true
                    at        = $ev.timestamp
                    type      = $ev.type
                }
            }
        }
        return @{ exhausted = $false }
    } catch {
        return @{ exhausted = $false; check_failed = $_.Exception.Message }
    }
}

function Test-RenderPipelineAvailable {
    param(
        [hashtable]$Headers,
        [string]$ServiceId
    )
    $status = Get-RenderPipelineStatus -Headers $Headers -ServiceId $ServiceId
    if ($status.exhausted) {
        return @{
            available = $false
            reason    = "pipeline_minutes_exhausted (quota build Render epuise ce mois-ci, ~2 min par deploy)"
        }
    }
    return @{ available = $true }
}

function Test-ApiCorsOrigin {
    param(
        [string]$ApiBaseUrl,
        [string]$Origin,
        [int]$MaxAttempts = 6,
        [int]$DelaySeconds = 5
    )
    $url = "$($ApiBaseUrl.TrimEnd('/'))/auth/required"
    for ($i = 1; $i -le $MaxAttempts; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $url -Headers @{ Origin = $Origin } -UseBasicParsing
            if ($r.Headers['Access-Control-Allow-Origin'] -eq $Origin) {
                return $true
            }
        } catch {
            # retry
        }
        if ($i -lt $MaxAttempts) {
            Start-Sleep -Seconds $DelaySeconds
        }
    }
    return $false
}