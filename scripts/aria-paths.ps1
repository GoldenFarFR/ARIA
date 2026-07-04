# Chemins monorepo ARIA + ops privé — source unique pour scripts PowerShell
$ErrorActionPreference = "Stop"

function Test-AriaMonorepoRoot {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path $Path)) { return $false }
    return (Test-Path (Join-Path $Path ".git")) -and (
        (Test-Path (Join-Path $Path "packages\aria-core")) -or
        (Test-Path (Join-Path $Path "vanguard"))
    )
}

function Test-AriaOpsRoot {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path $Path)) { return $false }
    return (Test-Path (Join-Path $Path ".git")) -and (Test-Path (Join-Path $Path "collegue-memoire"))
}

function Get-AriaRepoRoot {
    $candidates = @()
    if ($env:ARIA_REPO_ROOT) { $candidates += $env:ARIA_REPO_ROOT }
    $here = $PSScriptRoot
    if ($here -match 'local-sync\\scripts$') {
        $candidates += (Resolve-Path (Join-Path $here "..\..\..\ARIA")).Path
        $candidates += (Resolve-Path (Join-Path $here "..\..")).Path
    } elseif ($here -match 'aria-ops\\local-sync\\scripts$') {
        $candidates += (Resolve-Path (Join-Path $here "..\..\..\ARIA")).Path
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

function Get-AriaOpsRoot {
    $candidates = @()
    if ($env:ARIA_OPS_ROOT) { $candidates += $env:ARIA_OPS_ROOT }
    $here = $PSScriptRoot
    if ($here -match 'local-sync\\scripts$') {
        $candidates += (Resolve-Path (Join-Path $here "..\..")).Path
    } elseif ($here -match 'scripts$') {
        $sibling = Join-Path (Split-Path (Get-AriaRepoRoot) -Parent) "aria-ops"
        $candidates += $sibling
    }
    $candidates += (Join-Path $env:USERPROFILE "GitHub-Repos\aria-ops")
    foreach ($c in ($candidates | Select-Object -Unique)) {
        if (Test-AriaOpsRoot $c) {
            return (Resolve-Path $c).Path
        }
    }
    # Legacy: ops encore dans le monorepo ARIA (transition)
    $legacy = Get-AriaRepoRoot
    if (Test-Path (Join-Path $legacy "collegue-memoire")) {
        return $legacy
    }
    throw "ARIA_OPS_ROOT introuvable. Cloner GoldenFarFR/aria-ops (prive) a cote de ARIA."
}

$script:AriaRepoRoot = Get-AriaRepoRoot
$script:AriaOpsRoot = Get-AriaOpsRoot
$script:AriaVanguardRoot = Join-Path $script:AriaRepoRoot "vanguard"
$script:AriaOperatorRoot = Join-Path $script:AriaOpsRoot "vanguard\operator"
$script:AriaDataDir = Join-Path $script:AriaVanguardRoot "backend\data"
$script:AriaLocalSyncRoot = Join-Path $script:AriaOpsRoot "local-sync"
$script:AriaCollegueRoot = Join-Path $script:AriaOpsRoot "collegue-memoire"
$script:AriaSkillsRoot = Join-Path $script:AriaRepoRoot "skills"
$script:AriaCorePackage = Join-Path $script:AriaRepoRoot "packages\aria-core"
$script:AriaLettaRoot = Join-Path $script:AriaOpsRoot "letta-orchestrator"