# Helpers origine machine / IP pour audit securite

function Get-SessionsRoot {
    $pathsScript = Join-Path $PSScriptRoot "..\..\scripts\aria-paths.ps1"
    if (Test-Path $pathsScript) {
        . (Resolve-Path $pathsScript)
        return Join-Path $script:AriaCollegueRoot "sessions"
    }
    Join-Path (Join-Path $env:USERPROFILE "GitHub-Repos\ARIA\collegue-memoire") "sessions"
}

function Get-AllMachineIpRecords {
    $root = Get-SessionsRoot
    $out = @()
    if (-not (Test-Path $root)) { return $out }
    Get-ChildItem $root -Directory | ForEach-Object {
        $ipFile = Join-Path $_.FullName "ip-latest.json"
        if (-not (Test-Path $ipFile)) { return }
        try {
            $r = Get-Content $ipFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $out += $r
        } catch { }
    }
    return $out
}

function Get-MachineRegistry {
    $path = Join-Path $env:LOCALAPPDATA "GoldenFar\machine-ip-registry.json"
    $registry = @{}
    if (-not (Test-Path $path)) { return $registry }
    try {
        $raw = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
        $raw.PSObject.Properties | ForEach-Object {
            $registry[$_.Name] = $_.Value
        }
    } catch { }
    return $registry
}

function Test-IpKnownForMachine {
    param([string]$Machine, [string]$Ip, $Registry)
    if (-not $Registry.ContainsKey($Machine)) { return $false }
    $entry = $Registry[$Machine]
    if ($entry.ips -contains $Ip) { return $true }
    return $false
}