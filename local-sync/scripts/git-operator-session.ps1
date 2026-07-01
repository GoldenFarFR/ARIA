# Session operateur GitHub — TOTP une fois, valide 12h (pull/push scripts GoldenFar)
# Usage: . .\git-operator-session.ps1 ; Assert-GitOperatorSession

$totpGatePath = Join-Path $PSScriptRoot "totp-gate.ps1"
if (Test-Path $totpGatePath) {
    . $totpGatePath
}

$script:GitSessionHours = if ($env:GOLDENFAR_GIT_SESSION_HOURS) {
    [int]$env:GOLDENFAR_GIT_SESSION_HOURS
} else { 12 }

function Get-GitSessionPath {
    $dir = Join-Path $env:LOCALAPPDATA "GoldenFar"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Join-Path $dir "git-operator-session.json"
}

function Get-GitOperatorSession {
    $path = Get-GitSessionPath
    if (-not (Test-Path $path)) { return $null }
    try {
        $s = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($s.machine -ne $env:COMPUTERNAME) { return $null }
        $exp = [datetime]::Parse($s.expires_at)
        if ((Get-Date) -ge $exp) { return $null }
        return $s
    } catch {
        return $null
    }
}

function New-GitOperatorSession {
    $now = Get-Date
    $exp = $now.AddHours($script:GitSessionHours)
    $data = @{
        machine      = $env:COMPUTERNAME
        user         = $env:USERNAME
        validated_at = $now.ToString("yyyy-MM-ddTHH:mm:ss")
        expires_at   = $exp.ToString("yyyy-MM-ddTHH:mm:ss")
        ttl_hours    = $script:GitSessionHours
    }
    Set-Content -Path (Get-GitSessionPath) -Value ($data | ConvertTo-Json) -Encoding UTF8
    Write-Host "[GIT-SESSION] Active jusqu'a $($exp.ToString('yyyy-MM-dd HH:mm')) ($script:GitSessionHours h)" -ForegroundColor Green
    return $data
}

function Assert-GitOperatorSession {
    param(
        [switch]$SkipGate,
        [string]$TotpCode
    )
    if ($SkipGate) { return }

    $existing = Get-GitOperatorSession
    if ($existing) {
        Write-Host "[GIT-SESSION] Valide jusqu'a $($existing.expires_at)" -ForegroundColor DarkGray
        return
    }

    if (-not (Get-Command Assert-TotpGate -ErrorAction SilentlyContinue)) {
        Write-Host "[GIT-SESSION] totp-gate absent - gate desactive" -ForegroundColor Yellow
        return
    }
    $secret = Get-TotpSecret
    if (-not $secret) {
        Write-Host "[GIT-SESSION] TOTP non configure — gate desactive" -ForegroundColor Yellow
        return
    }

    Write-Host "[GIT-SESSION] Code Google Authenticator requis (session $script:GitSessionHours h)" -ForegroundColor Cyan
    Assert-TotpGate -Code $TotpCode
    New-GitOperatorSession | Out-Null
}

function Invoke-GoldenFarGitPull {
    param(
        [string]$Path,
        [switch]$SkipGitGate,
        [string]$TotpCode
    )
    Assert-GitOperatorSession -SkipGate:$SkipGitGate -TotpCode $TotpCode
    if (-not (Test-Path (Join-Path $Path ".git"))) { return $null }
    Push-Location $Path
    try {
        $before = (git rev-parse HEAD 2>$null).Trim()
        git pull --ff-only 2>&1 | Out-Null
        $after = (git rev-parse HEAD 2>$null).Trim()
        return @{ before = $before; after = $after; updated = ($before -ne $after) }
    } catch {
        return @{ error = $_.Exception.Message }
    } finally { Pop-Location }
}

function Invoke-GoldenFarGitPush {
    param(
        [string]$Path,
        [string]$Message,
        [string[]]$Add = @("."),
        [switch]$SkipGitGate,
        [string]$TotpCode
    )
    Assert-GitOperatorSession -SkipGate:$SkipGitGate -TotpCode $TotpCode
    Push-Location $Path
    try {
        foreach ($a in $Add) { git add $a 2>&1 | Out-Null }
        if (-not (git status --porcelain 2>$null)) {
            return @{ pushed = $false; reason = "nothing to commit" }
        }
        git commit -m $Message 2>&1 | Out-Null
        git push 2>&1 | Out-Null
        return @{ pushed = $true; commit = (git rev-parse --short HEAD 2>$null).Trim() }
    } catch {
        return @{ error = $_.Exception.Message }
    } finally { Pop-Location }
}