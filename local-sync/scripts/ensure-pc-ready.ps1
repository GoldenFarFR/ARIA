# Prepare le PC pour handoff Grok — clone repos manquants, detecte nouveau PC
# Appele par session-handoff.ps1 (Sylvain ne lance rien manuellement)
# Usage: .\ensure-pc-ready.ps1 [-SkipGitGate] [-TotpCode 123456]

param(
    [switch]$SkipGitGate,
    [string]$TotpCode
)

$ErrorActionPreference = "Stop"

$projets = Join-Path $env:USERPROFILE "projets"
$machine = $env:COMPUTERNAME
$localSync = Join-Path $projets "aria-local-sync"
$collegue = Join-Path $projets "collegue-memoire"
$bootJson = Join-Path $projets "collegue-memoire\sessions\$machine\boot-status.json"

if (-not (Test-Path $projets)) {
    New-Item -ItemType Directory -Path $projets -Force | Out-Null
    Write-Host "[BOOT] Cree $projets" -ForegroundColor Cyan
}

. (Join-Path $PSScriptRoot "git-operator-session.ps1")

$cloneOrder = @(
    @{ name = "aria-local-sync"; url = "https://github.com/GoldenFarFR/aria-local-sync.git" }
    @{ name = "collegue-memoire"; url = "https://github.com/GoldenFarFR/collegue-memoire.git" }
    @{ name = "aria-skills"; url = "https://github.com/GoldenFarFR/aria-skills.git" }
    @{ name = "aria-vanguard"; url = "https://github.com/GoldenFarFR/aria-vanguard.git" }
    @{ name = "aria-sandbox"; url = "https://github.com/GoldenFarFR/aria-sandbox.git" }
    @{ name = "template-grok-cursor"; url = "https://github.com/GoldenFarFR/template-grok-cursor.git" }
)

$cloned = @()
foreach ($repo in $cloneOrder) {
    $path = Join-Path $projets $repo.name
    if (-not (Test-Path (Join-Path $path ".git"))) {
        Write-Host "[CLONE] $($repo.name)..." -ForegroundColor Cyan
        Push-Location $projets
        try {
            git clone $repo.url $repo.name 2>&1 | Out-Null
            if (Test-Path (Join-Path $path ".git")) { $cloned += $repo.name }
        } catch {
            Write-Host "[WARN] clone $($repo.name) : $($_.Exception.Message)" -ForegroundColor Yellow
        } finally { Pop-Location }
    } else {
        $pull = Invoke-GoldenFarGitPull -Path $path -SkipGitGate:$SkipGitGate -TotpCode $TotpCode
        if ($pull -and $pull.updated) {
            Write-Host "[PULL] $($repo.name)" -ForegroundColor Green
        }
    }
}

# Regles IDE depuis collegue-memoire
$rulesSrc = Join-Path $collegue ".cursor\rules"
if (Test-Path $rulesSrc) {
    $cursorDst = Join-Path $env:USERPROFILE ".cursor\rules"
    $grokDst = Join-Path $env:USERPROFILE ".grok\rules"
    New-Item -ItemType Directory -Path $cursorDst -Force | Out-Null
    New-Item -ItemType Directory -Path $grokDst -Force | Out-Null
    Copy-Item (Join-Path $rulesSrc "*.md") $cursorDst -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $rulesSrc "*.md") $grokDst -Force -ErrorAction SilentlyContinue
    $skillRules = Join-Path $projets "aria-skills\.grok\rules"
    if (Test-Path $skillRules) {
        Copy-Item (Join-Path $skillRules "*.md") $grokDst -Force -ErrorAction SilentlyContinue
    }
}

$hasMaster = Test-Path (Join-Path $localSync ".vault-master-secret")
$hasTotp = Test-Path (Join-Path $localSync ".vault-totp-secret")
$hasVault = Test-Path (Join-Path $env:LOCALAPPDATA "GoldenFar\vault\production.env")
$stateFile = Join-Path $projets "collegue-memoire\sessions\$machine\handoff-state.json"
$isNewPc = (-not (Test-Path $stateFile)) -or (-not $hasVault)

$bootstrapRan = $false
$bootstrapError = $null
if ($isNewPc -and $hasMaster -and $hasTotp) {
    $bootstrap = Join-Path $PSScriptRoot "bootstrap-autre-pc.ps1"
    if (Test-Path $bootstrap) {
        Write-Host "[BOOT] Nouveau PC + secrets OK — bootstrap-autre-pc..." -ForegroundColor Cyan
        try {
            & $bootstrap -SkipHandoff -ErrorAction Stop
            $bootstrapRan = $true
        } catch {
            $bootstrapError = $_.Exception.Message
            Write-Host "[WARN] bootstrap : $bootstrapError" -ForegroundColor Yellow
        }
    }
} elseif ($isNewPc) {
    Write-Host "[BOOT] Nouveau PC — secrets Bitwarden manquants (Grok guide Sylvain)" -ForegroundColor Yellow
}

if (-not [Environment]::GetEnvironmentVariable("GOLDENFAR_VAULT_TOTP_VIA_ARIA", "User")) {
    [Environment]::SetEnvironmentVariable("GOLDENFAR_VAULT_TOTP_VIA_ARIA", "0", "User")
    $env:GOLDENFAR_VAULT_TOTP_VIA_ARIA = "0"
}

$status = [ordered]@{
    machine          = $machine
    at               = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    is_new_pc        = $isNewPc
    cloned_repos     = $cloned
    has_vault_secrets = ($hasMaster -and $hasTotp)
    has_local_vault  = $hasVault
    bootstrap_ran    = $bootstrapRan
    bootstrap_error  = $bootstrapError
    agent_next       = if ($isNewPc -and -not ($hasMaster -and $hasTotp)) {
        "Demander a Sylvain UNIQUEMENT les 2 fichiers Bitwarden (.vault-master-secret + .vault-totp-secret) puis relancer bootstrap-autre-pc.ps1"
    } elseif ($isNewPc -and -not $bootstrapRan) {
        "Executer bootstrap-autre-pc.ps1 puis check-aria-status.ps1"
    } else {
        "Lire HANDOFF.md + COLLEGUE.md — continuer la demande Sylvain"
    }
}

$dir = Split-Path $bootJson -Parent
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$status | ConvertTo-Json -Depth 4 | Set-Content -Path $bootJson -Encoding UTF8

return $status