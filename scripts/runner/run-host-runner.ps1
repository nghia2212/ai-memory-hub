<#
.SYNOPSIS
Launch the narrow authenticated V2 host runner (polls /v2/jobs/claim, no arbitrary shell).
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [string]$PythonExe = 'python',
    [string]$RunnerId = 'win-host-runner-1'
)

$ErrorActionPreference = 'Stop'
$HubRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$env:AI_MEMORY_RUNNER_ID = $RunnerId

if ($DryRun) {
    Write-Host "[DRY-RUN] Would run: $PythonExe -m services.orchestrator.dispatch.host_runner (cwd=$HubRoot)"
    exit 0
}

Push-Location $HubRoot
try {
    & $PythonExe -m services.orchestrator.dispatch.host_runner
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
