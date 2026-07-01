# Deploy Render UNIQUE — build local obligatoire, 1 redeploy groupe.
# Usage: .\deploy-render.ps1 -Reason "pin aria-core shadow judge"

param(
    [Parameter(Mandatory = $true)]
    [string]$Reason,
    [switch]$QuickBuild,
    [switch]$SkipPipelineCheck,
    [switch]$EnvOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $Root "_render-common.ps1")

$LogPath = Join-Path $Root "deploy-log.jsonl"

function Add-DeployLog($entry) {
    $line = ($entry | ConvertTo-Json -Compress)
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

Write-Host "=== deploy-render ===" -ForegroundColor Green
Write-Host "Raison: $Reason" -ForegroundColor Cyan

if (-not $EnvOnly) {
    $buildArgs = @{}
    if ($QuickBuild) { $buildArgs.Quick = $true }
    & (Join-Path $Root "build-local.ps1") @buildArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "build-local a echoue — deploy annule" -ForegroundColor Red
        exit 1
    }
}

$apiKey = Get-RenderApiKey -Root $Root
if ($apiKey) {
    $headers = Get-RenderHeaders -ApiKey $apiKey
    $serviceId = Find-RenderServiceId -Headers $headers -ServiceName "aria-api"
    if ($serviceId -and -not $SkipPipelineCheck -and -not $EnvOnly) {
        $pipe = Test-RenderPipelineAvailable -Headers $headers -ServiceId $serviceId
        if (-not $pipe.available) {
            Write-Host "[BLOQUE] $($pipe.reason)" -ForegroundColor Red
            Write-Host "Code OK en local — deploy quand quota Render reset ou upgrade Starter." -ForegroundColor Yellow
            Add-DeployLog @{
                at     = (Get-Date).ToUniversalTime().ToString("o")
                reason = $Reason
                status = "blocked"
                detail = $pipe.reason
            }
            exit 2
        }
    }
}

$syncArgs = @{}
if ($EnvOnly) { $syncArgs.SkipRedeploy = $true }

& (Join-Path $Root "sync-render.ps1") @syncArgs
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) { exit $exitCode }

& (Join-Path $Root "check-aria-status.ps1")
Add-DeployLog @{
    at       = (Get-Date).ToUniversalTime().ToString("o")
    reason   = $Reason
    status   = if ($EnvOnly) { "env_sync" } else { "deploy" }
    env_only = [bool]$EnvOnly
}

Write-Host "`nDeploy termine — raison enregistree dans deploy-log.jsonl" -ForegroundColor Green