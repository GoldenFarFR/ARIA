# Ouvrier — juge QI ARIA (métriques réelles, phase avant auto-évaluation)
$ErrorActionPreference = "Stop"
$Backend = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\backend"
Push-Location $Backend
try {
    python scripts/judge-aria-qi.py
} finally {
    Pop-Location
}