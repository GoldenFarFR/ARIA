# Retire le démarrage auto ARIA local
$TaskName = "GoldenFar-ARIA-Local-Bot"
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Tâche '$TaskName' supprimée." -ForegroundColor Green
} else {
    Write-Host "Aucune tâche '$TaskName'." -ForegroundColor Yellow
}