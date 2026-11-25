<#
.SYNOPSIS
Read-only status diagnostics for AI Memory Hub (Docker services, Host Brain Agent, Hub-to-Host link, P16B Recovery Queue, Auto-Start).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$HubRoot = Split-Path $PSScriptRoot -Parent
$envMap = @{}
$envPath = Join-Path $HubRoot '.env'
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') { $envMap[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
    }
}
$bridgePort = if ($envMap['BRIDGE_PORT']) { [int]$envMap['BRIDGE_PORT'] } else { 8082 }
$codebasePort = if ($envMap['CODEBASE_PORT']) { [int]$envMap['CODEBASE_PORT'] } else { 8081 }
$dashPort = if ($envMap['HUB_PORT']) { [int]$envMap['HUB_PORT'] } else { 8080 }
$hostPort = if ($envMap['HOST_BRAIN_PORT']) { [int]$envMap['HOST_BRAIN_PORT'] } else { 8084 }

$rows = @()
function Add-Row([string]$Comp, [string]$State, [string]$Detail) {
    $script:rows += [pscustomobject]@{ Component = $Comp; State = $State; Detail = $Detail }
}

# 1. Docker daemon
try {
    $dv = (& docker info --format '{{.ServerVersion}}' 2>$null).Trim()
    Add-Row 'Docker Daemon' $(if ($LASTEXITCODE -eq 0) { 'READY' } else { 'STOPPED' }) "Docker $dv"
} catch { Add-Row 'Docker Daemon' 'STOPPED' 'Không kết nối được Docker' }

# 2. Core HTTP healthz
foreach ($svc in @(
    @{ Name = 'Memory Bridge'; Url = "http://127.0.0.1:$bridgePort/healthz" },
    @{ Name = 'Codebase Service'; Url = "http://127.0.0.1:$codebasePort/healthz" },
    @{ Name = 'Dashboard UI'; Url = "http://127.0.0.1:$dashPort/healthz" },
    @{ Name = 'Host Brain Agent (Local)'; Url = "http://127.0.0.1:$hostPort/healthz" }
)) {
    try {
        $h = Invoke-RestMethod -Uri $svc.Url -TimeoutSec 3
        $extra = if ($h.schema_version) { "schema=v$($h.schema_version)" } else { "ready=$($h.ready)" }
        Add-Row $svc.Name $(if ($h.ready) { 'READY' } else { 'DEGRADED' }) "$($svc.Url) ($extra)"
    } catch {
        Add-Row $svc.Name 'OFFLINE' "$($svc.Url) không phản hồi"
    }
}

# 3. Hub-to-Host real link
try {
    $st = Invoke-RestMethod -Uri "http://127.0.0.1:$dashPort/api/status" -TimeoutSec 4
    Add-Row 'Hub -> Host Brain Link' $(if ($st.host_brain.ready) { 'CONNECTED' } else { 'DISCONNECTED' }) "$($st.host_brain.label): $($st.host_brain.detail)"
} catch {
    Add-Row 'Hub -> Host Brain Link' 'UNKNOWN' 'Dashboard offline'
}

# 4. Windows Login Auto-Start status (off by default)
$task = Get-ScheduledTask -TaskName 'AIMemoryHub-UserStartup' -ErrorAction SilentlyContinue
Add-Row 'Windows Login Auto-Start' $(if ($task) { $task.State.ToString() } else { 'DISABLED (Default)' }) $(if ($task) { 'Opt-in current-user Scheduled Task' } else { 'Bật tùy chọn qua scripts\Set-AIMemoryHubAutoStart.ps1 -Enable' })

$rows | Format-Table -AutoSize
