# Prepare le PC pour handoff Grok — clone monorepo ARIA, detecte nouveau PC
# Appele par session-handoff.ps1 (Sylvain ne lance rien manuellement)
# Usage: .\ensure-pc-ready.ps1 [-SkipGitGate] [-TotpCode 123456]

param(
    [switch]$SkipGitGate,
    [string]$TotpCode
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "git-operator-session.ps1")

$ariaDefault = Join-Path $env:USERPROFILE "GitHub-Repos\ARIA"
$ariaRepo = if ($env:ARIA_REPO_ROOT -and (Test-Path $env:ARIA_REPO_ROOT)) {
    $env:ARIA_REPO_ROOT
} elseif (Test-Path $ariaDefault) {
    $ariaDefault
} else {
    $parent = Join-Path $env:USERPROFILE "GitHub-Repos"
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    Write-Host "[CLONE] GoldenFarFR/ARIA (monorepo SSOT)..." -ForegroundColor Cyan
    Push-Location $parent
    try {
        git clone https://github.com/GoldenFarFR/ARIA.git ARIA 2>&1 | Out-Null
    } finally { Pop-Location }
    $ariaDefault
}

if (-not (Test-Path (Join-Path $ariaRepo ".git"))) {
    throw "Monorepo ARIA introuvable. git clone https://github.com/GoldenFarFR/ARIA.git $ariaDefault"
}

if (-not $env:ARIA_REPO_ROOT -or $env:ARIA_REPO_ROOT -ne $ariaRepo) {
    [Environment]::SetEnvironmentVariable("ARIA_REPO_ROOT", $ariaRepo, "User")
    $env:ARIA_REPO_ROOT = $ariaRepo
    Write-Host "[BOOT] ARIA_REPO_ROOT = $ariaRepo" -ForegroundColor DarkGray
}

$pull = Invoke-GoldenFarGitPull -Path $ariaRepo -SkipGitGate:$SkipGitGate -TotpCode $TotpCode
if ($pull -and $pull.updated) {
    Write-Host "[PULL] ARIA $($pull.before) -> $($pull.after)" -ForegroundColor Green
}

$machine = $env:COMPUTERNAME
$collegue = Join-Path $ariaRepo "collegue-memoire"
$localSync = Join-Path $ariaRepo "local-sync"
$bootJson = Join-Path $collegue "sessions\$machine\boot-status.json"

# Regles IDE depuis monorepo
$rulesSrc = Join-Path $collegue ".cursor\rules"
if (Test-Path $rulesSrc) {
    $cursorDst = Join-Path $env:USERPROFILE ".cursor\rules"
    $grokDst = Join-Path $env:USERPROFILE ".grok\rules"
    New-Item -ItemType Directory -Path $cursorDst -Force | Out-Null
    New-Item -ItemType Directory -Path $grokDst -Force | Out-Null
    Copy-Item (Join-Path $rulesSrc "*.md") $cursorDst -Force -ErrorAction SilentlyContinue
    Copy-Item (Join-Path $rulesSrc "*.md") $grokDst -Force -ErrorAction SilentlyContinue
    $skillRules = Join-Path $ariaRepo "skills\.grok\rules"
    if (Test-Path $skillRules) {
        Copy-Item (Join-Path $skillRules "*.md") $grokDst -Force -ErrorAction SilentlyContinue
    }
}

$hasMaster = Test-Path (Join-Path $localSync ".vault-master-secret")
$hasTotp = Test-Path (Join-Path $localSync ".vault-totp-secret")
$hasVault = Test-Path (Join-Path $env:LOCALAPPDATA "GoldenFar\vault\production.env")
$stateFile = Join-Path $collegue "sessions\$machine\handoff-state.json"
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
    machine           = $machine
    at                = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    aria_repo_root    = $ariaRepo
    is_new_pc         = $isNewPc
    cloned_repos      = @("ARIA")
    has_vault_secrets = ($hasMaster -and $hasTotp)
    has_local_vault   = $hasVault
    bootstrap_ran     = $bootstrapRan
    bootstrap_error   = $bootstrapError
    agent_next        = if ($isNewPc -and -not ($hasMaster -and $hasTotp)) {
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