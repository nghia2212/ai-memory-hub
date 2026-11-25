<#
.SYNOPSIS
Idempotent, safe `.env` bootstrap script for AI Memory Hub.

.DESCRIPTION
Generates missing local internal tokens and dashboard secrets into ignored `.env`
without overwriting existing non-empty values and without printing secret values.
Hermes stays disabled by default (`HERMES_ENABLED=false`).
#>
[CmdletBinding()]
param(
    [string]$EnvFilePath = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$HubRoot = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($EnvFilePath)) {
    $EnvFilePath = Join-Path $HubRoot '.env'
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding $false
$Timestamp = Get-Date -Format 'yyyyMMddHHmmss'

function New-RandomHex {
    param([int]$Bytes = 32)
    $buf = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($buf)
    } finally {
        $rng.Dispose()
    }
    return ([System.BitConverter]::ToString($buf) -replace '-', '').ToLowerInvariant()
}

$envMap = [ordered]@{}
if (Test-Path -LiteralPath $EnvFilePath) {
    foreach ($line in (Get-Content -LiteralPath $EnvFilePath -Encoding UTF8)) {
        $trimmed = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('#')) {
            continue
        }
        $idx = $trimmed.IndexOf('=')
        if ($idx -gt 0) {
            $k = $trimmed.Substring(0, $idx).Trim()
            $v = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
            $envMap[$k] = $v
        }
    }
}

$defaults = [ordered]@{
    'HUB_PORT'                  = '8080'
    'CODEBASE_PORT'             = '8081'
    'BRIDGE_PORT'               = '8082'
    'DASHBOARD_ALLOWED_ORIGINS' = 'http://127.0.0.1:8080,http://localhost:8080'
    'HERMES_ENABLED'            = 'false'
    'HERMES_PROVIDER'           = 'deepseek'
    'DEEPSEEK_API_KEY'          = ''
    'DEEPSEEK_BASE_URL'         = 'https://api.deepseek.com'
    'DEEPSEEK_MODEL'            = 'deepseek-flash'
    'HERMES_APPROVED_PROJECTS'  = ''
}

$secretKeys = @(
    'BRIDGE_READ_TOKEN',
    'BRIDGE_WRITE_TOKEN',
    'BRIDGE_ADMIN_TOKEN',
    'BRIDGE_WORKER_TOKEN',
    'BRIDGE_PLANNER_TOKEN',
    'BRIDGE_RUNNER_TOKEN',
    'CODEBASE_TOKEN',
    'CODEBASE_READ_TOKEN',
    'CODEBASE_ADMIN_TOKEN',
    'DASHBOARD_PASSWORD',
    'DASHBOARD_SESSION_SECRET'
)

$added = New-Object System.Collections.Generic.List[string]
foreach ($k in $defaults.Keys) {
    if (-not $envMap.Contains($k)) {
        $envMap[$k] = $defaults[$k]
        $added.Add($k)
    }
}

foreach ($sk in $secretKeys) {
    if (-not $envMap.Contains($sk) -or [string]::IsNullOrWhiteSpace($envMap[$sk])) {
        if ($sk -eq 'CODEBASE_READ_TOKEN' -and $envMap.Contains('CODEBASE_TOKEN') -and -not [string]::IsNullOrWhiteSpace($envMap['CODEBASE_TOKEN'])) {
            $envMap[$sk] = $envMap['CODEBASE_TOKEN']
        } elseif ($sk -eq 'DASHBOARD_PASSWORD') {
            $envMap[$sk] = New-RandomHex -Bytes 16
        } else {
            $envMap[$sk] = New-RandomHex -Bytes 32
        }
        $added.Add($sk)
    }
}

if ($added.Count -eq 0 -and (Test-Path -LiteralPath $EnvFilePath)) {
    Write-Host "Environment file '$EnvFilePath' already has all required keys (no changes)."
    exit 0
}

if ($DryRun) {
    Write-Host "[DRY-RUN] Would update '$EnvFilePath' adding keys: $($added -join ', ')"
    exit 0
}

if (Test-Path -LiteralPath $EnvFilePath) {
    $bak = "$EnvFilePath.bak.$Timestamp"
    Copy-Item -LiteralPath $EnvFilePath -Destination $bak -Force
}

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('# Generated/updated by scripts/init-env.ps1 (ignored by Git; never commit secrets)')
foreach ($k in $envMap.Keys) {
    $lines.Add("$k=$($envMap[$k])")
}
$tmp = "$EnvFilePath.tmp.$PID.$Timestamp"
[System.IO.File]::WriteAllText($tmp, (($lines -join "`n") + "`n"), $Utf8NoBom)
Move-Item -LiteralPath $tmp -Destination $EnvFilePath -Force

Write-Host "Initialized/updated '$EnvFilePath' safely (added keys: $($added -join ', '); secrets not printed)."
