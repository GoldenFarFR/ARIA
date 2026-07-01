# Sync vanguard.env vers Render (static site aria-vanguard)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")
. (Join-Path $Root "_site-common.ps1")

$EnvFile = Get-VanguardEnvPath -ScriptsRoot $Root
$ExampleFile = Join-Path $Root "vanguard.env.example"
$ServiceName = "aria-vanguard"

$apiKey = Get-RenderApiKey -Root $Root
if (-not $apiKey) {
    Write-Host "Cle Render manquante (coffre keys\render.api-key)." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $ExampleFile) {
        Copy-Item $ExampleFile $EnvFile
        Write-Host "vanguard.env cree depuis vanguard.env.example - edite si besoin." -ForegroundColor Yellow
    } else {
        Write-Host "vanguard.env manquant." -ForegroundColor Red
        exit 1
    }
}

$headers = Get-RenderHeaders -ApiKey $apiKey
$config = Get-Content (Join-Path $Root "site.config.json") -Raw | ConvertFrom-Json

$serviceId = if ($config.vanguardRenderServiceId) { $config.vanguardRenderServiceId } else { Find-RenderServiceId -Headers $headers -ServiceName $ServiceName }
if (-not $serviceId) {
    Write-Host "Service Render '$ServiceName' introuvable. Lance .\setup-vanguard-render.ps1" -ForegroundColor Red
    exit 1
}

# Persist service id for next runs
$cfgPath = Join-Path $Root "site.config.json"
$cfgObj = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfgObj | Add-Member -NotePropertyName vanguardRenderServiceId -NotePropertyValue $serviceId -Force
($cfgObj | ConvertTo-Json -Depth 4) + "`n" | Set-Content -Path $cfgPath -Encoding UTF8 -NoNewline

Write-Host "Service aria-vanguard: $serviceId" -ForegroundColor Green

$vars = Read-EnvFile -Path $EnvFile
$toSync = @{}
foreach ($key in $vars.Keys) {
    if ($vars[$key]) { $toSync[$key] = $vars[$key] }
}

if ($toSync.Count -eq 0) {
    Write-Host "Aucune variable dans vanguard.env" -ForegroundColor Yellow
    exit 0
}

$ok = 0
foreach ($key in $toSync.Keys) {
    $body = (@{ value = $toSync[$key] } | ConvertTo-Json -Compress)
    $url = "https://api.render.com/v1/services/$serviceId/env-vars/$key"
    try {
        Invoke-RestMethod -Uri $url -Method Put -Headers $headers -Body $body | Out-Null
        Write-Host "[OK] $key" -ForegroundColor Green
        $ok++
    } catch {
        Write-Host "[ERREUR] $key : $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "$ok variable(s) Vanguard -> Render." -ForegroundColor Cyan
Write-Host "Redemarrage aria-vanguard (obligatoire pour rebuild avec nouvelles vars)..." -ForegroundColor Cyan
try {
    $deploy = Start-RenderServiceDeploy -Headers $headers -ServiceId $serviceId
    if ($deploy.id) {
        Write-Host "Deploy lance: $($deploy.id)" -ForegroundColor Green
        Wait-RenderServiceDeploy -Headers $headers -ServiceId $serviceId -DeployId $deploy.id -TimeoutSeconds 300 | Out-Null
        Write-Host "aria-vanguard live." -ForegroundColor Green
    }
} catch {
    Write-Host "[WARN] Redemarrage auto echoue: $($_.Exception.Message)" -ForegroundColor Yellow
}