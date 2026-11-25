<#
.SYNOPSIS
Start or check the Windows Host Brain Agent (loopback 127.0.0.1:8084) for AI Memory Hub V2.
#>
[CmdletBinding()]
param(
    [int]$Port = 8084
)

$ErrorActionPreference = 'Stop'
$HubRoot = Split-Path $PSScriptRoot -Parent
$ScriptPath = Join-Path $HubRoot 'services\orchestrator\host_brain_agent.py'

try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2
    if ($h.ready) {
        Write-Host "Host Brain Agent already running on http://127.0.0.1:$Port"
        exit 0
    }
} catch {}

$LogOut = Join-Path $HubRoot 'data\host-brain-agent.out.log'
$LogErr = Join-Path $HubRoot 'data\host-brain-agent.err.log'
$null = New-Item -ItemType Directory -Path (Join-Path $HubRoot 'data') -Force
Start-Process -FilePath 'python' -ArgumentList @($ScriptPath) -WorkingDirectory $HubRoot -WindowStyle Hidden -RedirectStandardOutput $LogOut -RedirectStandardError $LogErr

for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Milliseconds 400
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2
        if ($h.ready) {
            Write-Host "Host Brain Agent started on http://127.0.0.1:$Port (ready=True)"
            exit 0
        }
    } catch {}
}
throw "Host Brain Agent failed to start on port $Port. Check $LogErr"
