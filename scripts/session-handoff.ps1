# Redirect — SSOT : aria-ops/local-sync/scripts/session-handoff.ps1
$opsHandoff = if ($env:ARIA_OPS_ROOT) {
    Join-Path $env:ARIA_OPS_ROOT "local-sync\scripts\session-handoff.ps1"
} else {
    Join-Path $env:USERPROFILE "GitHub-Repos\aria-ops\local-sync\scripts\session-handoff.ps1"
}
if (-not (Test-Path $opsHandoff)) {
    throw "Cloner GoldenFarFR/aria-ops (prive) puis definir ARIA_OPS_ROOT."
}
& $opsHandoff @PSBoundParameters