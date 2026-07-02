# Chemins monorepo ARIA — source unique pour scripts PowerShell
$ErrorActionPreference = "Stop"

function Get-AriaRepoRoot {
    if ($env:ARIA_REPO_ROOT -and (Test-Path $env:ARIA_REPO_ROOT)) {
        return (Resolve-Path $env:ARIA_REPO_ROOT).Path
    }
    $here = $PSScriptRoot
    if ($here -match 'local-sync\\scripts$') {
        return (Resolve-Path (Join-Path $here "..\..")).Path
    }
    if ($here -match 'scripts$') {
        return (Resolve-Path (Join-Path $here "..")).Path
    }
    $default = Join-Path $env:USERPROFILE "GitHub-Repos\ARIA"
    if (Test-Path $default) {
        return (Resolve-Path $default).Path
    }
    throw "ARIA_REPO_ROOT introuvable. Definir `$env:ARIA_REPO_ROOT ou cloner GoldenFarFR/ARIA."
}

$script:AriaRepoRoot = Get-AriaRepoRoot
$script:AriaVanguardRoot = Join-Path $script:AriaRepoRoot "vanguard"
$script:AriaOperatorRoot = Join-Path $script:AriaVanguardRoot "operator"
$script:AriaDataDir = Join-Path $script:AriaVanguardRoot "backend\data"
$script:AriaLocalSyncRoot = Join-Path $script:AriaRepoRoot "local-sync"
$script:AriaCollegueRoot = Join-Path $script:AriaRepoRoot "collegue-memoire"
$script:AriaSkillsRoot = Join-Path $script:AriaRepoRoot "skills"
$script:AriaCorePackage = Join-Path $script:AriaRepoRoot "packages\aria-core"
$script:AriaLettaRoot = Join-Path $script:AriaRepoRoot "letta-orchestrator"