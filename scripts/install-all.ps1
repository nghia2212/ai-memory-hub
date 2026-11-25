<#
.SYNOPSIS
Unified Windows installer for AI Memory Hub across Claude Code, Gemini CLI, and Codex CLI.

.DESCRIPTION
Auto-detects installed agent CLIs and their versions, allows selecting specific agents
or all/auto-detected agents, and delegates to install-claude.ps1, install-gemini.ps1,
and install-codex.ps1 with -DryRun, -Uninstall, and -Force support.
#>
[CmdletBinding()]
param(
    [string[]]$Agents = @('auto'),
    [switch]$DryRun,
    [switch]$Uninstall,
    [switch]$Force,
    [string]$EnvFilePath = ''
)

$ErrorActionPreference = 'Stop'

function Get-DetectedAgents {
    $detected = [ordered]@{}
    foreach ($name in @('claude', 'gemini', 'codex')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            $ver = ((& $name --version 2>$null) -join ' ').Trim()
            if ([string]::IsNullOrWhiteSpace($ver)) { $ver = 'installed (version unknown)' }
            $detected[$name] = @{ installed = $true; version = $ver; path = $cmd.Source }
        } else {
            $detected[$name] = @{ installed = $false; version = 'not installed'; path = '' }
        }
    }
    return $detected
}

$inventory = Get-DetectedAgents
Write-Host "=== AI Memory Hub CLI Detection ==="
foreach ($k in $inventory.Keys) {
    $info = $inventory[$k]
    Write-Host ("  {0,-8}: {1}" -f $k, $info.version)
}

$normalized = @()
foreach ($a in $Agents) {
    foreach ($part in ($a -split ',')) {
        $p = $part.Trim().ToLowerInvariant()
        if ($p) { $normalized += $p }
    }
}

$targets = New-Object System.Collections.Generic.List[string]
if ($normalized -contains 'all') {
    $targets.AddRange([string[]]@('claude', 'gemini', 'codex'))
} elseif ($normalized -contains 'auto') {
    foreach ($k in $inventory.Keys) {
        if ($inventory[$k].installed) { $targets.Add($k) }
    }
    if ($targets.Count -eq 0) {
        Write-Host "No supported agent CLIs detected on PATH. Specify -Agents claude,gemini,codex to configure anyway."
        exit 0
    }
} else {
    foreach ($t in $normalized) {
        if ($t -notin @('claude', 'gemini', 'codex')) {
            throw "Unsupported agent '$t'. Valid values: claude, gemini, codex, all, auto."
        }
        if (-not $targets.Contains($t)) { $targets.Add($t) }
    }
}

$scriptMap = @{
    'claude' = Join-Path $PSScriptRoot 'install-claude.ps1'
    'gemini' = Join-Path $PSScriptRoot 'install-gemini.ps1'
    'codex'  = Join-Path $PSScriptRoot 'install-codex.ps1'
}

foreach ($agent in $targets) {
    Write-Host "`n--- Running installer for $agent ---"
    $params = @{}
    if ($DryRun) { $params['DryRun'] = $true }
    if ($Uninstall) { $params['Uninstall'] = $true }
    if ($Force) { $params['Force'] = $true }
    if (-not [string]::IsNullOrWhiteSpace($EnvFilePath)) { $params['EnvFilePath'] = $EnvFilePath }
    & $scriptMap[$agent] @params
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
        throw "Installer for '$agent' failed with exit code $LASTEXITCODE"
    }
}

Write-Host "`nAll selected installers finished ($($targets -join ', '))."
