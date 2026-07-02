# Smoke tests Letta — validation tâches réelles (simple / moyen / complexe)
param(
    [switch]$Quick
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$LogPath = Join-Path $Here "smoke-results.jsonl"
$py = Join-Path $Here "venv\Scripts\python.exe"
$orch = Join-Path $Here "orchestrate.py"

if (-not (Test-Path $py)) { throw "venv absent — lance .\install.ps1" }

$scenarios = @(
    @{
        id = "simple-greeting"
        niveau = $null
        message = "Dis bonjour en une phrase."
        expect_niveau = "simple"
    },
    @{
        id = "moyen-debug-path"
        niveau = $null
        message = "Explique comment diagnostiquer pourquoi aria-cursor-bridge.ps1 ne trouve pas le vault GoldenFar sur Windows."
        expect_niveau = "moyen"
    },
    @{
        id = "complexe-refactor"
        niveau = if ($Quick) { "complexe" } else { $null }
        message = if ($Quick) {
            "Propose un plan en 5 étapes pour refactorer letta-orchestrator/orchestrate.py et aria-letta-integration.ps1 vers un seul point d'entrée."
        } else {
            "Refactorise l'architecture Letta : fusionne orchestrate.py, orchestrate.ps1 et aria-letta-integration.ps1 en un point d'entrée unique avec routage visible, tests smoke et README. Liste les fichiers touchés et les risques."
        }
        expect_niveau = "complexe"
    }
)

function Get-RoutingFromOutput([string]$raw) {
    $line = ($raw -split "`n" | Where-Object { $_ -match '^ARIA_ROUTING_JSON=' } | Select-Object -Last 1)
    if (-not $line) { return $null }
    try {
        return ($line -replace '^ARIA_ROUTING_JSON=', '') | ConvertFrom-Json
    } catch { return $null }
}

Write-Host "`n=== ARIA Letta smoke tests ===" -ForegroundColor Cyan
$passed = 0
$failed = 0

foreach ($s in $scenarios) {
    Write-Host "`n[$($s.id)] ..." -ForegroundColor Yellow
    $args = @($orch, "--message", $s.message)
    if ($s.niveau) { $args += @("--niveau", $s.niveau) }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $out = & $py @args 2>&1 | Out-String
    $sw.Stop()

    $routing = Get-RoutingFromOutput $out
    $ok = $routing -and $routing.success
    if ($ok -and $s.expect_niveau -and $routing.niveau -ne $s.expect_niveau -and $s.niveau -eq $null) {
        Write-Host "  niveau attendu $($s.expect_niveau), obtenu $($routing.niveau) (warning)" -ForegroundColor DarkYellow
    }

    $entry = [ordered]@{
        ts = (Get-Date).ToString("o")
        id = $s.id
        ok = [bool]$ok
        wall_seconds = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        routing = if ($routing) { $routing } else { $null }
    }
    ($entry | ConvertTo-Json -Compress -Depth 6) | Add-Content -Path $LogPath -Encoding UTF8

    if ($ok) {
        $passed++
        $agent = if ($routing) { $routing.agent } else { "?" }
        Write-Host "  OK — $agent ($($sw.Elapsed.TotalSeconds.ToString('0.0'))s)" -ForegroundColor Green
    } else {
        $failed++
        Write-Host "  FAIL — voir $LogPath" -ForegroundColor Red
    }
}

Write-Host "`nRésumé : $passed OK, $failed FAIL — log: $LogPath" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Red" })
if ($failed -gt 0) { exit 1 }