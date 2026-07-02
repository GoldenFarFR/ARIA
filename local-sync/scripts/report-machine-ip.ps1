# Enregistre l'IP publique du PC pour audit multi-origine
# Usage: .\report-machine-ip.ps1

$ErrorActionPreference = "Stop"

. (Resolve-Path (Join-Path $PSScriptRoot "..\..\scripts\aria-paths.ps1"))

$machine = $env:COMPUTERNAME
$collegue = $script:AriaCollegueRoot
$machineDir = Join-Path (Join-Path $collegue "sessions") $machine
$ipLatest = Join-Path $machineDir "ip-latest.json"
$cacheLocal = Join-Path $env:LOCALAPPDATA "GoldenFar\machine-ip-cache.json"

function Get-PublicIp {
    if (Test-Path $cacheLocal) {
        try {
            $c = Get-Content $cacheLocal -Raw -Encoding UTF8 | ConvertFrom-Json
            $age = (Get-Date) - [datetime]::Parse($c.fetched_at)
            if ($age.TotalMinutes -lt 30 -and $c.public_ip) {
                return $c.public_ip
            }
        } catch { }
    }
    $ip = $null
    foreach ($url in @("https://api.ipify.org", "https://icanhazip.com")) {
        try {
            $ip = (Invoke-RestMethod -Uri $url -TimeoutSec 8).ToString().Trim()
            if ($ip -match '^\d{1,3}(\.\d{1,3}){3}$') { break }
        } catch { $ip = $null }
    }
    if (-not $ip) { return $null }
    @{
        public_ip  = $ip
        fetched_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    } | ConvertTo-Json | Set-Content -Path $cacheLocal -Encoding UTF8
    return $ip
}

$publicIp = Get-PublicIp
if (-not $publicIp) {
    Write-Host "[IP] Impossible de resoudre l'IP publique" -ForegroundColor Yellow
    return $null
}

if (-not (Test-Path $machineDir)) {
    New-Item -ItemType Directory -Path $machineDir -Force | Out-Null
}

$prevIp = $null
if (Test-Path $ipLatest) {
    try { $prevIp = (Get-Content $ipLatest -Raw -Encoding UTF8 | ConvertFrom-Json).public_ip } catch { }
}

$record = @{
    machine     = $machine
    user        = $env:USERNAME
    public_ip   = $publicIp
    reported_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
    ip_changed  = ($prevIp -and $prevIp -ne $publicIp)
    previous_ip = $prevIp
}
Set-Content -Path $ipLatest -Value ($record | ConvertTo-Json) -Encoding UTF8

$registryPath = Join-Path $env:LOCALAPPDATA "GoldenFar\machine-ip-registry.json"
$registry = @{}
if (Test-Path $registryPath) {
    try {
        $raw = Get-Content $registryPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $raw.PSObject.Properties | ForEach-Object { $registry[$_.Name] = $_.Value }
    } catch { }
}
$key = "$machine"
$ips = @()
if ($registry.ContainsKey($key)) {
    $ips = @($registry[$key].ips)
}
if ($ips -notcontains $publicIp) {
    $ips += $publicIp
}
$registry[$key] = @{
    machine    = $machine
    ips        = $ips
    last_ip    = $publicIp
    updated_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
}
$registry | ConvertTo-Json -Depth 4 | Set-Content -Path $registryPath -Encoding UTF8

if ($record.ip_changed) {
    Write-Host "[IP] Changement detecte: $prevIp -> $publicIp" -ForegroundColor Yellow
} else {
    Write-Host "[IP] $machine = $publicIp" -ForegroundColor DarkGray
}

return $record