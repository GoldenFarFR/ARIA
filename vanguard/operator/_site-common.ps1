# Site URL automation - dot-source from set-site.ps1 / sync-render.ps1

. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "_vault-common.ps1")

function Get-SiteConfig {
    param([string]$Root)
    $path = Join-Path $Root "site.config.json"
    if (-not (Test-Path $path)) {
        throw "site.config.json manquant dans $Root"
    }
    return Get-Content $path -Raw | ConvertFrom-Json
}

function Normalize-SiteUrl {
    param([string]$DomainOrUrl)
    $v = $DomainOrUrl.Trim().TrimEnd("/")
    if ($v -match '^https?://') { return $v }
    return "https://$v"
}

function Set-SiteConfig {
    param(
        [string]$Root,
        [string]$ServiceId,
        [string]$ServiceName,
        [string]$SiteBaseUrl,
        [string]$RepoPath = "..",
        [string]$CustomDomain = "",
        [string]$RenderBaseUrl = "",
        [string]$HoldingDomain = "",
        [string]$HoldingSiteUrl = ""
    )
    $path = Join-Path $Root "site.config.json"
    $existing = @{}
    if (Test-Path $path) {
        $existing = Get-Content $path -Raw | ConvertFrom-Json -AsHashtable
    }
    $obj = [ordered]@{
        renderServiceId   = $ServiceId
        renderServiceName = $ServiceName
        dexpulseRepoPath  = if ($existing.dexpulseRepoPath) { $existing.dexpulseRepoPath } else { $RepoPath }
        siteBaseUrl       = $SiteBaseUrl
    }
    if ($existing.vanguardRepoPath) { $obj.vanguardRepoPath = $existing.vanguardRepoPath }
    if ($existing.vanguardRepo) { $obj.vanguardRepo = $existing.vanguardRepo }
    if ($existing.vanguardRenderServiceName) { $obj.vanguardRenderServiceName = $existing.vanguardRenderServiceName }
    if ($HoldingDomain) { $obj.holdingDomain = $HoldingDomain }
    elseif ($existing.holdingDomain) { $obj.holdingDomain = $existing.holdingDomain }
    if ($HoldingSiteUrl) { $obj.holdingSiteUrl = $HoldingSiteUrl }
    elseif ($existing.holdingSiteUrl) { $obj.holdingSiteUrl = $existing.holdingSiteUrl }
    if ($existing.holdingApiUrl) { $obj.holdingApiUrl = $existing.holdingApiUrl }
    if ($existing.holdingApiDomain) { $obj.holdingApiDomain = $existing.holdingApiDomain }
    if ($CustomDomain) { $obj.customDomain = $CustomDomain }
    if ($RenderBaseUrl) { $obj.renderBaseUrl = $RenderBaseUrl }
    ($obj | ConvertTo-Json -Depth 3) + "`n" | Set-Content -Path $path -Encoding UTF8 -NoNewline
}

function Get-VanguardRepoPath {
    param([string]$SecretsRoot, $Config)
    $rel = if ($Config.vanguardRepoPath) { $Config.vanguardRepoPath } else { ".." }
    return (Resolve-Path (Join-Path $SecretsRoot $rel)).Path
}

function Get-DexpulseRepoPath {
    param([string]$SecretsRoot, $Config)
    return Get-VanguardRepoPath -SecretsRoot $SecretsRoot -Config $Config
}

function Get-RenderService {
    param(
        [hashtable]$Headers,
        [string]$ServiceId
    )
    return Invoke-RestMethod -Uri "https://api.render.com/v1/services/$ServiceId" -Headers $Headers -Method Get
}

function Get-RenderPublicUrl {
    param($Service)
    $url = $Service.serviceDetails.url
    if ($url) { return $url.TrimEnd("/") }
    if ($Service.slug) { return "https://$($Service.slug).onrender.com" }
    return ""
}

function Get-ExpectedRenderUrl {
    param([string]$ServiceName)
    $slug = ($ServiceName -replace '[^a-zA-Z0-9-]', '-').ToLower()
    return "https://$slug.onrender.com"
}

function Update-EnvFileKey {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Value
    )
    if (-not (Test-Path $Path)) { return }
    $lines = Get-Content $Path
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
            $found = $true
            "$Key=$Value"
        } else {
            $line
        }
    }
    if (-not $found) { $out += "$Key=$Value" }
    Set-Content -Path $Path -Value $out -Encoding UTF8
}

function Update-RenderYamlSiteUrl {
    param(
        [string]$RepoPath,
        [string]$ServiceName,
        [string]$SiteUrl,
        [string]$HoldingDomain = ""
    )
    $yaml = Join-Path $RepoPath "render.yaml"
    if (-not (Test-Path $yaml)) { return }
    $content = Get-Content $yaml -Raw
    $content = $content -replace '(?m)^(\s*name:\s*).*$', "`${1}$ServiceName"
    $content = $content -replace '(?m)^(\s*- key: SITE_BASE_URL\s*\r?\n\s*value:\s*).*$', "`${1}$SiteUrl"
    if ($HoldingDomain) {
        if ($content -match '(?m)^\s*- key: HOLDING_DOMAIN') {
            $content = $content -replace '(?m)^(\s*- key: HOLDING_DOMAIN\s*\r?\n\s*value:\s*).*$', "`${1}$HoldingDomain"
        } else {
            $content = $content -replace '(?m)(- key: SITE_BASE_URL\s*\r?\n\s*value:\s*.+)', "`$1`n      - key: HOLDING_DOMAIN`n        value: $HoldingDomain"
        }
    }
    Set-Content -Path $yaml -Value $content -Encoding UTF8 -NoNewline
}

function Update-ReadmeSiteUrl {
    param(
        [string]$RepoPath,
        [string]$SiteUrl
    )
    $readme = Join-Path $RepoPath "README.md"
    if (-not (Test-Path $readme)) { return }
    $content = Get-Content $readme -Raw
    $content = $content -replace 'https://[a-zA-Z0-9-]+\.onrender\.com', $SiteUrl
    Set-Content -Path $readme -Value $content -Encoding UTF8 -NoNewline
}

function Update-SiteUrlEverywhere {
    param(
        [string]$SecretsRoot,
        [string]$RepoPath,
        [string]$ServiceName,
        [string]$SiteUrl,
        [string]$HoldingDomain = "",
        [string]$HoldingSiteUrl = ""
    )
    Update-EnvFileKey -Path (Get-ProductionEnvPath -ScriptsRoot $SecretsRoot) -Key "SITE_BASE_URL" -Value $SiteUrl
    Update-EnvFileKey -Path (Join-Path $SecretsRoot "production.env.example") -Key "SITE_BASE_URL" -Value $SiteUrl
    if ($HoldingDomain) {
        Update-EnvFileKey -Path (Get-ProductionEnvPath -ScriptsRoot $SecretsRoot) -Key "HOLDING_DOMAIN" -Value $HoldingDomain
        Update-EnvFileKey -Path (Join-Path $SecretsRoot "production.env.example") -Key "HOLDING_DOMAIN" -Value $HoldingDomain
    }
    Update-RenderYamlSiteUrl -RepoPath $RepoPath -ServiceName $ServiceName -SiteUrl $SiteUrl -HoldingDomain $HoldingDomain
    Update-ReadmeSiteUrl -RepoPath $RepoPath -SiteUrl $SiteUrl

    $envExample = Join-Path $RepoPath "backend\.env.example"
    if (Test-Path $envExample) {
        $c = Get-Content $envExample -Raw
        $c = $c -replace '(?m)^SITE_BASE_URL=.*$', "SITE_BASE_URL=$SiteUrl"
        if ($c -notmatch '(?m)^SITE_BASE_URL=') { $c += "`nSITE_BASE_URL=$SiteUrl" }
        if ($HoldingDomain) {
            $c = $c -replace '(?m)^HOLDING_DOMAIN=.*$', "HOLDING_DOMAIN=$HoldingDomain"
            if ($c -notmatch '(?m)^HOLDING_DOMAIN=') { $c += "`nHOLDING_DOMAIN=$HoldingDomain" }
        }
        Set-Content -Path $envExample -Value $c -Encoding UTF8 -NoNewline
    }

    if ($HoldingDomain) {
        $holdingUrl = if ($HoldingSiteUrl) { $HoldingSiteUrl } else { "https://$HoldingDomain" }
        $siteTs = Join-Path $RepoPath "product-frontend\src\lib\site.ts"
        if (-not (Test-Path $siteTs)) { $siteTs = Join-Path $RepoPath "frontend\src\lib\site.ts" }
        if (Test-Path $siteTs) {
            $c = Get-Content $siteTs -Raw
            $c = $c -replace "export const HOLDING_DOMAIN = '[^']+'", "export const HOLDING_DOMAIN = '$HoldingDomain'"
            $c = $c -replace 'export const HOLDING_SITE_URL = `https://[^`]+`', "export const HOLDING_SITE_URL = ``$holdingUrl``"
            Set-Content -Path $siteTs -Value $c -Encoding UTF8 -NoNewline
        }
    }

    $productDomain = ($SiteUrl -replace '^https?://', '').Split('/')[0]
    $indexHtml = Join-Path $RepoPath "product-frontend\index.html"
    if (-not (Test-Path $indexHtml)) { $indexHtml = Join-Path $RepoPath "frontend\index.html" }
    if (Test-Path $indexHtml) {
        $c = Get-Content $indexHtml -Raw
        $c = $c -replace 'href="https://[^"]+/\"', "href=`"$SiteUrl/`""
        $c = $c -replace 'content="https://[^"]+/"', "content=`"$SiteUrl/`""
        $c = $c -replace 'twitter:domain" content="[^"]+"', "twitter:domain`" content=`"$productDomain`""
        Set-Content -Path $indexHtml -Value $c -Encoding UTF8 -NoNewline
    }
}

function Invoke-RenderServiceRename {
    param(
        [hashtable]$Headers,
        [string]$ServiceId,
        [string]$NewName
    )
    $body = (@{ name = $NewName } | ConvertTo-Json -Compress)
    return Invoke-RestMethod -Uri "https://api.render.com/v1/services/$ServiceId" -Method Patch -Headers $Headers -Body $body
}

function Invoke-RenderSuspendService {
    param(
        [hashtable]$Headers,
        [string]$ServiceId
    )
    return Invoke-RestMethod -Uri "https://api.render.com/v1/services/$ServiceId/suspend" -Method Post -Headers $Headers
}

function Invoke-RenderCreateDeploy {
    param(
        [hashtable]$Headers,
        [string]$ServiceId
    )
    $body = (@{ clearCache = "do_not_clear" } | ConvertTo-Json -Compress)
    return Invoke-RestMethod -Uri "https://api.render.com/v1/services/$ServiceId/deploys" -Method Post -Headers $Headers -Body $body
}

function New-RenderDexPulseService {
    param(
        [hashtable]$Headers,
        [string]$OwnerId,
        [string]$Name,
        [string]$Repo,
        [string]$Branch,
        [string]$Region,
        [hashtable]$EnvVars
    )
    $createBody = @{
        type           = "web_service"
        name           = $Name
        ownerId        = $OwnerId
        repo           = $Repo
        branch         = $Branch
        autoDeploy     = "yes"
        serviceDetails = @{
            env                = "docker"
            plan               = "free"
            region             = $Region
            healthCheckPath    = "/api/health"
            envSpecificDetails = @{
                dockerfilePath = "./Dockerfile"
                dockerContext  = "."
            }
        }
    }
    $json = $createBody | ConvertTo-Json -Depth 8 -Compress
    $created = Invoke-RestMethod -Uri "https://api.render.com/v1/services" -Method Post -Headers $Headers -Body $json
    Start-Sleep -Seconds 5

    foreach ($key in $EnvVars.Keys) {
        if (-not $EnvVars[$key]) { continue }
        $body = (@{ value = $EnvVars[$key] } | ConvertTo-Json -Compress)
        $url = "https://api.render.com/v1/services/$($created.id)/env-vars/$key"
        $ok = $false
        for ($i = 0; $i -lt 5; $i++) {
            try {
                Invoke-RestMethod -Uri $url -Method Put -Headers $Headers -Body $body | Out-Null
                $ok = $true
                break
            } catch {
                Start-Sleep -Seconds 3
            }
        }
        if (-not $ok) {
            Write-Host "[WARN] Env var $key not set on new service" -ForegroundColor Yellow
        }
    }

    $diskBody = (@{
        name      = "aria-api-data"
        mountPath = "/app/backend/data"
        sizeGB    = 1
    } | ConvertTo-Json -Compress)
    try {
        Invoke-RestMethod -Uri "https://api.render.com/v1/services/$($created.id)/disks" -Method Post -Headers $Headers -Body $diskBody | Out-Null
    } catch {
        Write-Host "[WARN] Disk attach failed (add manually in dashboard): $($_.Exception.Message)" -ForegroundColor Yellow
    }

    return $created
}

function Resolve-RenderServiceId {
    param(
        [hashtable]$Headers,
        [string]$Root,
        [string]$FallbackName = "aria-api"
    )
    try {
        $cfg = Get-SiteConfig -Root $Root
        if ($cfg.renderServiceId) {
            return $cfg.renderServiceId
        }
    } catch { }
    return (Find-RenderServiceId -Headers $Headers -ServiceName $FallbackName)
}