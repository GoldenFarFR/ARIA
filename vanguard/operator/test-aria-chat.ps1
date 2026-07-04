# Test rapide API chat ARIA (apres start-acp-local.ps1)
# Usage : .\test-aria-chat.ps1

$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:8000/api/aria/chat"

Write-Host "Test chat ARIA..." -ForegroundColor Cyan

$null = Invoke-RestMethod -Uri $base -Method POST -Body (@{
    message = "cree un tweet"
} | ConvertTo-Json -Compress) -ContentType "application/json" -TimeoutSec 60

$r = Invoke-RestMethod -Uri $base -Method POST -Body (@{
    message = "tu as gagne de l'argent sur acp aujourd'hui ?"
} | ConvertTo-Json -Compress) -ContentType "application/json" -TimeoutSec 90

Write-Host "acp=$($r.data.acp) compose=$($r.data.compose_workflow) skill=$($r.skill_used)" -ForegroundColor Green
$preview = $r.reply.Substring(0, [Math]::Min(250, $r.reply.Length))
Write-Host $preview