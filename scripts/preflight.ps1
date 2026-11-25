$ErrorActionPreference = 'Stop'
function Invoke-Checked { param([string]$Program, [string[]]$Arguments) & $Program @Arguments; if ($LASTEXITCODE -ne 0) { throw "$Program failed: $LASTEXITCODE" } }
Invoke-Checked 'wsl.exe' @('--status')
Invoke-Checked 'wsl.exe' @('--list','--verbose')
Invoke-Checked 'docker' @('version')
Invoke-Checked 'docker' @('info')
Invoke-Checked 'docker' @('compose','version')
Push-Location (Split-Path $PSScriptRoot -Parent)
try { Invoke-Checked 'docker' @('compose','config','--quiet') } finally { Pop-Location }
