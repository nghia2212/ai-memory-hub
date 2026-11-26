<# Compatibility wrapper. Hermes is now managed by the Hub's unified launcher. #>
[CmdletBinding()]
param([switch]$StartDockerDesktop,[switch]$DryRun,[int]$HealthTimeoutSec=180)
$ErrorActionPreference = 'Stop'
$launcher = Join-Path (Split-Path $PSScriptRoot -Parent) 'Start-AIMemoryHub.ps1'
& $launcher -StartDockerDesktop:$StartDockerDesktop -DryRun:$DryRun -HealthTimeoutSec $HealthTimeoutSec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
