# CNAME api.ariavanguardzhc.com -> test-1-nwf2.onrender.com (IONOS DNS API)
# Prerequis: .ionos-api-key (une ligne: prefix.secret) via .\register-ionos-api-key.ps1
# Doc: https://developer.hosting.ionos.fr/docs/dns

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Domain = "ariavanguardzhc.com"
$Subdomain = "api"
$Fqdn = "$Subdomain.$Domain"
$Target = "test-1-nwf2.onrender.com"
. (Join-Path $Root "_vault-common.ps1")
$KeyFile = Get-IonosApiKeyPath -ScriptsRoot $Root
$IonosApi = "https://api.hosting.ionos.com/dns/v1"

function Get-IonosHeaders {
    param([string]$ApiKey)
    return @{
        "X-API-Key"    = $ApiKey
        Accept         = "application/json"
        "Content-Type" = "application/json"
    }
}

function Invoke-Ionos {
    param(
        [string]$Method,
        [string]$Route,
        [string]$Body = ""
    )
    $uri = "$IonosApi$Route"
    $params = @{
        Uri     = $uri
        Method  = $Method
        Headers = $script:IonosHeaders
    }
    if ($Body) { $params.Body = $Body }
    return Invoke-RestMethod @params
}

Write-Host "=== DNS IONOS: $Fqdn -> $Target ===" -ForegroundColor Cyan

if (-not (Test-Path $KeyFile)) {
    Write-Host ""
    Write-Host "Cle API absente. Lance :" -ForegroundColor Yellow
    Write-Host "  .\register-ionos-api-key.ps1"
    Write-Host ""
    Write-Host "Ou manuel (2 min) sur https://my.ionos.com -> $Domain -> DNS :" -ForegroundColor Green
    Write-Host "  CNAME | Nom: api | Cible: $Target"
    exit 0
}

$apiKey = (Get-Content $KeyFile -Raw).Trim()
$script:IonosHeaders = Get-IonosHeaders -ApiKey $apiKey

Write-Host "Cle IONOS detectee - API DNS..." -ForegroundColor Cyan
try {
    $zones = Invoke-Ionos -Method Get -Route "/zones"
    $zoneList = @($zones)
    if ($zones -is [pscustomobject] -and $zones.name) {
        $zoneList = @($zones)
    }
    $zone = $zoneList | Where-Object { $_.name -eq $Domain } | Select-Object -First 1
    if (-not $zone) {
        Write-Host "Zone $Domain introuvable sur ce compte IONOS." -ForegroundColor Yellow
        Write-Host "Verifie que la cle est creee avec le bon compte (my.ionos.com)." -ForegroundColor Yellow
        exit 1
    }
    $zoneId = $zone.id
    Write-Host "Zone: $($zone.name) ($zoneId)"

    $zoneDetail = Invoke-Ionos -Method Get -Route "/zones/$zoneId"
    $records = @()
    if ($zoneDetail.records) { $records = @($zoneDetail.records) }

    $existing = $records | Where-Object {
        $_.type -eq "CNAME" -and (
            $_.name -eq $Fqdn -or $_.name -eq "$Fqdn." -or $_.name -eq $Subdomain
        )
    } | Select-Object -First 1

    if ($existing) {
        if ($existing.content -eq $Target -or $existing.content -eq "$Target.") {
            Write-Host "[OK] CNAME deja correct: $($existing.name) -> $($existing.content)" -ForegroundColor Green
        } else {
            Write-Host "CNAME existant: $($existing.content) -> mise a jour vers $Target" -ForegroundColor Yellow
            $patchBody = @(
                @{
                    name     = $Fqdn
                    type     = "CNAME"
                    content  = $Target
                    ttl      = 3600
                    prio     = 0
                    disabled = $false
                }
            ) | ConvertTo-Json -Compress
            Invoke-Ionos -Method Put -Route "/zones/$zoneId/records/$($existing.id)" -Body $patchBody | Out-Null
            Write-Host "[OK] CNAME mis a jour." -ForegroundColor Green
        }
    } else {
        $body = @(
            @{
                name     = $Fqdn
                type     = "CNAME"
                content  = $Target
                ttl      = 3600
                prio     = 0
                disabled = $false
            }
        ) | ConvertTo-Json -Compress
        Invoke-Ionos -Method Post -Route "/zones/$zoneId/records" -Body $body | Out-Null
        Write-Host "[OK] CNAME api cree via API IONOS." -ForegroundColor Green
    }
} catch {
    $msg = $_.Exception.Message
    if ($_.ErrorDetails.Message) { $msg = $_.ErrorDetails.Message }
    Write-Host "[ERREUR] API IONOS: $msg" -ForegroundColor Red
    if ($msg -match '401|403|Unauthorized') {
        Write-Host "Cle invalide ou expiree -> .\register-ionos-api-key.ps1" -ForegroundColor Yellow
    }
    exit 1
}

Write-Host "Verification DNS (2 min)..." -ForegroundColor DarkGray
Start-Sleep -Seconds 120
try {
    Resolve-DnsName $Fqdn -Type CNAME -Server 8.8.8.8 -ErrorAction Stop | Format-Table
    Write-Host "[OK] DNS visible." -ForegroundColor Green
    Write-Host "Suite: .\setup-holding-api.ps1 puis .\update-stripe-webhook-url.ps1" -ForegroundColor Cyan
} catch {
    Write-Host "[WARN] DNS pas encore visible - normal 5-15 min. Render passera en verified ensuite." -ForegroundColor Yellow
}