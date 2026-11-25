<#
.SYNOPSIS
Coordinated safe Stop for AI Memory Hub (Docker Compose + owned Host Brain Agent).
NEVER runs `docker compose down -v`, NEVER deletes volumes, and checks active jobs before stopping.
#>
[CmdletBinding()]
param(
    [switch]$HubOnly,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$HubRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $HubRoot

function Read-EnvMap([string]$Path) {
    $map = @{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
                $map[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $map
}

try {
    $envMap = Read-EnvMap (Join-Path $HubRoot '.env')
    $bridgePort = if ($envMap['BRIDGE_PORT']) { [int]$envMap['BRIDGE_PORT'] } else { 8082 }

    # 1. Check active running tasks before stopping (protect active work!)
    $activeCount = 0
    if ($envMap['BRIDGE_READ_TOKEN']) {
        try {
            $hdr = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_READ_TOKEN']) }
            $projs = (Invoke-RestMethod -Uri "http://127.0.0.1:$bridgePort/v1/projects" -Headers $hdr -TimeoutSec 4).items
            foreach ($p in $projs) {
                $sess = (Invoke-RestMethod -Uri "http://127.0.0.1:$bridgePort/v2/sessions?project_id=$($p.project_id)" -Headers $hdr -TimeoutSec 4).items
                $activeCount += @($sess | Where-Object { $_.status -eq 'running' }).Count
            }
        } catch {}
    }

    if ($activeCount -gt 0 -and !$Force) {
        Write-Warning "[ACTIVE_WORK_DETECTED] Có $activeCount phiên Agent đang chạy. Không dừng đột ngột khi chưa có xác nhận (-Force). Các IDE/CLI bên ngoài cũng không bị dừng tự động."
        exit 10
    }

    if ($DryRun) {
        Write-Host "[DRYRUN] Sẽ chạy 'docker compose stop codebase memory-bridge dashboard' (giữ nguyên toàn bộ volumes)." -ForegroundColor Cyan
        if (!$HubOnly) { Write-Host "[DRYRUN] Sẽ dừng đúng tiến trình host_brain_agent.py thuộc repo (không chạm tiến trình lạ)." -ForegroundColor Cyan }
        exit 0
    }

    Write-Host "[1/2] Dừng các container Docker (bảo toàn 100% SQLite volumes — KHÔNG dùng down -v)..." -ForegroundColor Cyan
    & docker compose stop codebase memory-bridge dashboard

    if (!$HubOnly) {
        Write-Host "[2/2] Dừng Host Brain Agent thuộc AI Memory Hub (không dừng IDE/CLI hay tiến trình lạ)..." -ForegroundColor Cyan
        $owned = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'host_brain_agent\.py' -and $_.Name -match '^python' })
        foreach ($p in $owned) {
            Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
            Write-Host "  Đã dừng host_brain_agent.py (PID=$($p.ProcessId))." -ForegroundColor Green
        }
        $pidFile = Join-Path $HubRoot 'data\host-brain-agent.pid'
        if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue }
    } else {
        Write-Host "[2/2] Chế độ -HubOnly: giữ nguyên Host Brain Agent trên host." -ForegroundColor Yellow
    }

    Write-Host "AI Memory Hub đã dừng an toàn (toàn bộ dữ liệu SQLite, checkpoint & mã nguồn được bảo toàn)." -ForegroundColor Green
} finally {
    Pop-Location
}
