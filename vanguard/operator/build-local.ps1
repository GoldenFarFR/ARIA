# Validation locale avant deploy Render — reproduit les etapes critiques du Dockerfile.
# Usage: .\build-local.ps1 [-Quick] [-SkipFrontend]

param(
    [switch]$Quick,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$OperatorRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VanguardRoot = Split-Path -Parent $OperatorRoot
$MonorepoRoot = Split-Path -Parent $VanguardRoot
$Backend = Join-Path $VanguardRoot "backend"
$Frontend = Join-Path $VanguardRoot "product-frontend"
$CorePackage = Join-Path $MonorepoRoot "packages\aria-core"

function Write-Step($msg) {
    Write-Host "`n== $msg ==" -ForegroundColor Cyan
}

Write-Host "=== build-local (aria-vanguard) ===" -ForegroundColor Green
Write-Host "Politique: build local a chaque changement code ; deploy Render seulement via deploy-render.ps1" -ForegroundColor DarkGray

Write-Step "aria-core — pip install (monorepo packages/aria-core)"
if (-not (Test-Path $CorePackage)) { throw "aria-core introuvable: $CorePackage" }
python -m pip install -q -e $CorePackage
if ($LASTEXITCODE -ne 0) { throw "pip install aria-core a echoue" }

Write-Step "Backend — pip install requirements.txt"
$req = Join-Path $Backend "requirements.txt"
if (-not (Test-Path $req)) { throw "requirements.txt introuvable: $req" }
python -m pip install -q -r $req
if ($LASTEXITCODE -ne 0) { throw "pip install a echoue" }

Write-Step "Backend — import smoke (comme Dockerfile)"
Push-Location $Backend
$env:PYTHONPATH = $Backend
python -c "from app.main import app; print('import check ok')"
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "import app.main a echoue" }
Pop-Location

if (-not $Quick -and -not $SkipFrontend) {
    Write-Step "Product frontend — npm ci + build"
    if (-not (Test-Path (Join-Path $Frontend "package.json"))) {
        throw "product-frontend introuvable"
    }
    Push-Location $Frontend
    npm ci --no-audit --no-fund 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm ci a echoue" }
    npm run build 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "npm run build a echoue" }
    Pop-Location
} else {
    Write-Host "[SKIP] frontend (Quick ou SkipFrontend)" -ForegroundColor Yellow
}

Write-Step "aria-core — pytest rapide (si installe)"
$coreTests = Join-Path $CorePackage "tests"
if (Test-Path $coreTests) {
    Push-Location $CorePackage
    python -m pytest tests -q --tb=no -x 2>&1 | Out-Host
    $pytestExit = $LASTEXITCODE
    Pop-Location
    if ($pytestExit -ne 0) {
        Write-Host "[WARN] pytest aria-core a echoue — corriger avant deploy" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "[SKIP] aria-core tests (repo sandbox absent)" -ForegroundColor DarkGray
}

Write-Host "`nbuild-local OK — pret pour deploy-render.ps1 si necessaire" -ForegroundColor Green
exit 0