# Tache planifiee — rotation + push du coffre chiffre chaque nuit (03:00)
# Usage: .\setup-daily-vault-task.ps1

$ErrorActionPreference = "Stop"
$rotate = Join-Path $PSScriptRoot "rotate-daily-vault.ps1"
$taskName = "GoldenFar-DailyVaultRotation"

if (-not (Test-Path (Join-Path $PSScriptRoot "daily-vault-key.ps1"))) {
    throw "daily-vault-key.ps1 introuvable"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$rotate`""
$trigger = New-ScheduledTaskTrigger -Daily -At 3am
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Rechiffre goldenfar-vault.gfv avec cle du jour" -Force | Out-Null

Write-Host "[OK] Tache: $taskName (tous les jours 03:00)" -ForegroundColor Green
Write-Host "Prerequis: secret maitre dans .vault-master-secret ou GOLDENFAR_VAULT_MASTER" -ForegroundColor DarkGray
Write-Host "Test manuel: .\rotate-daily-vault.ps1" -ForegroundColor DarkGray