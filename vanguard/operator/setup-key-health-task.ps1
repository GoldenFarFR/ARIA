# Tache planifiee hebdo — verifie que les cles Render/local/health sont OK
# Usage: .\setup-key-health-task.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$check = Join-Path $Root "check-aria-status.ps1"
$taskName = "GoldenFar-KeyHealth"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$check`""
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Audit cles ARIA (Render vs coffre vs health)" -Force | Out-Null

Write-Host "[OK] Tache planifiee: $taskName (lundis 9h)" -ForegroundColor Green
Write-Host "Test manuel: .\check-aria-status.ps1" -ForegroundColor DarkGray