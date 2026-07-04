# Append one line to the Aria activity journal (local time, French format)
param(
    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$JournalPath = $(if ($env:ARIA_OPS_ROOT) {
        Join-Path $env:ARIA_OPS_ROOT "collegue-memoire\JOURNAL.md"
    } elseif ($env:ARIA_REPO_ROOT) {
        Join-Path $env:ARIA_REPO_ROOT "collegue-memoire\JOURNAL.md"
    } else {
        Join-Path $env:USERPROFILE "GitHub-Repos\aria-ops\collegue-memoire\JOURNAL.md"
    }),

    [string]$RepoJournal
)

$ErrorActionPreference = "Stop"

function Write-JournalLine {
    param([string]$Path, [string]$Line)

    $dir = Split-Path $Path -Parent
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    $today = Get-Date -Format "yyyy-MM-dd"
    $section = "## $today"
    $header = "# Journal de bord Aria`n`n"

    if (-not (Test-Path $Path)) {
        Set-Content -Path $Path -Value ($header + $section + "`n" + $Line + "`n") -Encoding utf8
        return
    }

    $content = Get-Content -Path $Path -Raw -Encoding utf8
    if ($content -notmatch [regex]::Escape($section)) {
        if (-not $content.EndsWith("`n")) { $content += "`n" }
        $content += "`n$section`n$Line`n"
        Set-Content -Path $Path -Value $content -Encoding utf8 -NoNewline
    } else {
        if (-not $content.EndsWith("`n")) { $content += "`n" }
        $content += "$Line`n"
        Set-Content -Path $Path -Value $content -Encoding utf8 -NoNewline
    }
}

$time = Get-Date -Format "HH\hmm"
$sep = [char]0x2014  # em dash
$line = "$time $sep $Message"

Write-JournalLine -Path $JournalPath -Line $line

if ($RepoJournal) {
    Write-JournalLine -Path $RepoJournal -Line $line
}

Write-Output $line