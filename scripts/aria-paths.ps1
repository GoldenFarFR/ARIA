# Chemins monorepo ARIA — source unique pour scripts PowerShell
$ErrorActionPreference = "Stop"

function Test-AriaMonorepoRoot {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path $Path)) { return $false }
    return (Test-Path (Join-Path $Path ".git")) -and (Test-Path (Join-Path $Path "collegue-memoire"))
}

function Get-AriaRepoRoot {
    $candidates = @()
    if ($env:ARIA_REPO_ROOT) { $candidates += $env:ARIA_REPO_ROOT }
    $here = $PSScriptRoot
    if ($here -match 'local-sync\\scripts$') {
        $candidates += (Resolve-Path (Join-Path $here "..\..")).Path
    } elseif ($here -match 'scripts$') {
        $candidates += (Resolve-Path (Join-Path $here "..")).Path
    }
    $candidates += (Join-Path $env:USERPROFILE "GitHub-Repos\ARIA")
    foreach ($c in ($candidates | Select-Object -Unique)) {
        if (Test-AriaMonorepoRoot $c) {
            return (Resolve-Path $c).Path
        }
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