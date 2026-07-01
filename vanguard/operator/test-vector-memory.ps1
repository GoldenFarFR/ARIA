# Délègue au script SSOT aria-core
$CoreScript = Join-Path $env:USERPROFILE "projets\aria-sandbox\packages\aria-core\scripts\test-vector-memory.ps1"
if (-not (Test-Path $CoreScript)) {
    Write-Host "aria-core introuvable: $CoreScript" -ForegroundColor Red
    exit 1
}
& $CoreScript @PSBoundParameters