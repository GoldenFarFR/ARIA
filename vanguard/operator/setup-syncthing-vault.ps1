# Syncthing — partage le coffre GoldenFar (multi-PC)
# Usage: .\setup-syncthing-vault.ps1 [-OpenGui]

param([switch]$OpenGui)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_vault-common.ps1")

$vault = Get-GoldenFarVaultRoot
$stHome = Join-Path $env:LOCALAPPDATA "Syncthing"
$cfgPath = Join-Path $stHome "config.xml"

function Find-SyncthingExe {
    $cmd = Get-Command syncthing -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $winget = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Filter "syncthing.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($winget) { return $winget.FullName }
    return $null
}

Write-Host "=== Syncthing + coffre GoldenFar ===" -ForegroundColor Cyan

$exe = Find-SyncthingExe
if (-not $exe) {
    Write-Host "Syncthing absent. Lance: winget install Syncthing.Syncthing" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $cfgPath)) {
    Write-Host "Premier demarrage Syncthing..." -ForegroundColor Yellow
    Start-Process -FilePath $exe -ArgumentList "serve","--no-browser","--home=$stHome" -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline -and -not (Test-Path $cfgPath)) { Start-Sleep 2 }
}

if (-not (Test-Path $cfgPath)) { throw "config.xml Syncthing introuvable" }

$running = Get-Process syncthing -ErrorAction SilentlyContinue
if (-not $running) {
    Start-Process -FilePath $exe -ArgumentList "serve","--no-browser","--home=$stHome" -WindowStyle Hidden
    Start-Sleep 5
}

$cfg = [xml](Get-Content $cfgPath)
$apiKey = $cfg.configuration.gui.apikey
$deviceId = $cfg.configuration.device.id
$deviceName = $cfg.configuration.device.name
$headers = @{ "X-API-Key" = $apiKey }

$deadline = (Get-Date).AddSeconds(30)
do {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8384/rest/system/status" -Headers $headers -TimeoutSec 3 | Out-Null
        break
    } catch {
        Start-Sleep 2
    }
} while ((Get-Date) -lt $deadline)

$existing = Invoke-RestMethod -Uri "http://127.0.0.1:8384/rest/config/folders" -Headers $headers
$found = $existing | Where-Object { $_.id -eq "goldenfar-vault" -or $_.path -eq $vault }
if (-not $found) {
    $folder = [ordered]@{
        id               = "goldenfar-vault"
        label            = "GoldenFar Vault"
        path             = $vault
        type             = "sendreceive"
        devices          = @([ordered]@{ deviceID = $deviceId })
        rescanIntervalS  = 3600
        fsWatcherEnabled = $true
        fsWatcherDelayS  = 10
        ignorePerms      = $false
        autoNormalize    = $true
    }
    $all = @($existing) + @($folder)
    $json = $all | ConvertTo-Json -Depth 6 -Compress
    Invoke-RestMethod -Uri "http://127.0.0.1:8384/rest/config/folders" -Headers $headers -Method Put -Body $json -ContentType "application/json" | Out-Null
    Write-Host "[OK] Dossier goldenfar-vault ajoute." -ForegroundColor Green
} else {
    Write-Host "[OK] Dossier deja configure." -ForegroundColor Green
}

Write-Host ""
Write-Host "PC        : $deviceName" -ForegroundColor Cyan
Write-Host "ID appareil (a ajouter sur l'autre PC) :" -ForegroundColor Yellow
Write-Host "  $deviceId"
Write-Host ""
Write-Host "Coffre    : $vault"
Write-Host "Interface : http://127.0.0.1:8384"
Write-Host ""
Write-Host "Sur l'autre PC :" -ForegroundColor Green
Write-Host "  1. winget install Syncthing.Syncthing"
Write-Host "  2. Ajouter appareil distant -> coller l'ID ci-dessus"
Write-Host "  3. Accepter le dossier goldenfar-vault -> chemin identique vault"
Write-Host "  4. .\new-pc.ps1"

if ($OpenGui) { Start-Process "http://127.0.0.1:8384" }