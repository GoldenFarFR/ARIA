# Installe le démarrage auto ARIA local (PC toujours allumé, pas Render)
param([switch]$Force)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Wrapper = Join-Path $Root "start-acp-local-boot.ps1"
$TaskName = "GoldenFar-ARIA-Local-Bot"

if (-not (Test-Path $Wrapper)) { throw "Absent: $Wrapper" }

$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = (Get-Command powershell -ErrorAction Stop).Source }

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and -not $Force) {
    Write-Host "Tâche '$TaskName' existe déjà — relance avec -Force" -ForegroundColor Yellow
    exit 0
}
if ($existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }

$action = New-ScheduledTaskAction `
    -Execute $pwsh `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Wrapper`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT90S"

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Démarre ARIA bot local (:8000) après connexion Windows — runtime PC, pas Render." `
    -RunLevel Limited | Out-Null

Write-Host "OK — tâche planifiée '$TaskName'" -ForegroundColor Green
Write-Host "  Déclencheur : connexion + 90s (Ollama/sync)" -ForegroundColor DarkGray
Write-Host "  Log : $env:LOCALAPPDATA\GoldenFar\aria-boot.log" -ForegroundColor DarkGray
Write-Host "  Désinstaller : .\uninstall-aria-local-boot.ps1" -ForegroundColor DarkGray