<#
.SYNOPSIS
Unified Windows Startup Launcher for AI Memory Hub V3.2.1 (P16B.4).
Starts required Docker Compose services (preserving volumes) + Windows Host Brain Agent (single-instance),
verifies actual Hub-to-Host reachability, and triggers P16B startup recovery scan.
#>
[CmdletBinding()]
param(
    [switch]$StartDockerDesktop,
    [switch]$SkipRecoveryScan,
    [switch]$LegacyOnly,
    [int]$HealthTimeoutSec = 90,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$HubRoot = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $HubRoot

function Write-Step([string]$Msg) { Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Msg) -ForegroundColor Cyan }
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
function Wait-EndpointReady([string]$Url, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $h = Invoke-RestMethod -Uri $Url -TimeoutSec 4
            if ($h -and $h.ready -eq $true) { return $h }
        } catch {}
        Start-Sleep -Seconds 2
    }
    throw "[HEALTH_TIMEOUT] Quá thời gian chờ (${TimeoutSec}s): $Url"
}

try {
    Write-Step '1/6 Kiểm tra cấu hình .env và Docker...'
    $envPath = Join-Path $HubRoot '.env'
    $envMap = Read-EnvMap $envPath
    if (!(Test-Path -LiteralPath $envPath -PathType Leaf) -or
        (!$LegacyOnly -and (!$envMap['HERMES_NATIVE_DASHBOARD_PASSWORD'] -or !$envMap['HERMES_NATIVE_DASHBOARD_SECRET']))) {
        if ($DryRun) {
            Write-Host '[DRYRUN] Would initialize missing local environment keys; no credentials written.'
        } else {
            & (Join-Path $PSScriptRoot 'init-env.ps1') -EnvFilePath $envPath
            if ($LASTEXITCODE -ne 0) { throw 'Local environment bootstrap failed.' }
            $envMap = Read-EnvMap $envPath
            if (!$LegacyOnly -and (!$envMap['HERMES_NATIVE_DASHBOARD_PASSWORD'] -or !$envMap['HERMES_NATIVE_DASHBOARD_SECRET'])) {
                throw 'Missing Hermes dashboard authentication configuration.'
            }
        }
    }
    $hermesPort = if ($envMap['HERMES_NATIVE_PORT']) { [int]$envMap['HERMES_NATIVE_PORT'] } else { 9119 }
    $bridgePort = if ($envMap['BRIDGE_PORT']) { [int]$envMap['BRIDGE_PORT'] } else { 8082 }
    $codebasePort = if ($envMap['CODEBASE_PORT']) { [int]$envMap['CODEBASE_PORT'] } else { 8081 }
    $dashPort = if ($envMap['HUB_PORT']) { [int]$envMap['HUB_PORT'] } else { 8080 }
    $hostPort = if ($envMap['HOST_BRAIN_PORT']) { [int]$envMap['HOST_BRAIN_PORT'] } else { 8084 }

    if (!(Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "[MISSING_DOCKER] Không tìm thấy lệnh 'docker' trên PATH. Hãy cài đặt Docker Desktop."
    }

    $dockerReady = $false
    try {
        $null = & docker info --format '{{.ServerVersion}}' 2>$null
        if ($LASTEXITCODE -eq 0) { $dockerReady = $true }
    } catch {}

    if (!$dockerReady) {
        if ($StartDockerDesktop) {
            $ddExe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
            if (!(Test-Path -LiteralPath $ddExe)) {
                throw "[DOCKER_UNAVAILABLE] Docker Desktop chưa chạy và không tìm thấy '$ddExe'."
            }
            if (!$DryRun) {
                Write-Step 'Đang khởi động Docker Desktop (theo tham số -StartDockerDesktop)...'
                Start-Process -FilePath $ddExe
                $ddDeadline = (Get-Date).AddSeconds($HealthTimeoutSec)
                while ((Get-Date) -lt $ddDeadline) {
                    Start-Sleep -Seconds 3
                    try {
                        $null = & docker info --format '{{.ServerVersion}}' 2>$null
                        if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
                    } catch {}
                }
            }
        }
        if (!$dockerReady -and !$DryRun) {
            throw "[DOCKER_UNAVAILABLE] Docker daemon chưa sẵn sàng. Hãy mở Docker Desktop hoặc truyền -StartDockerDesktop."
        }
    }

    if ($DryRun) {
        if (!$LegacyOnly) { Write-Host "[DRYRUN] Would start official Hermes with Hub (port $hermesPort, persistent volume)." }
        Write-Host "[DRYRUN] Docker OK | Ports: Bridge=$bridgePort, Codebase=$codebasePort, Dashboard=$dashPort, HostAgent=$hostPort" -ForegroundColor Green
        & (Join-Path $PSScriptRoot 'start-host-brain-agent.ps1') -Port $hostPort -DryRun
        exit 0
    }

    Write-Step '2/6 Khởi động các container bắt buộc (bảo toàn 100% SQLite volumes, không bật optional MCP/IDE)...'
    $services = @('codebase','memory-bridge','dashboard')
    if (!$LegacyOnly) { $services += 'hermes-native' }
    & docker compose up -d @services
    if ($LASTEXITCODE -ne 0) {
        throw "[COMPOSE_UP_FAILED] docker compose up -d thất bại (exit=$LASTEXITCODE)."
    }

    Write-Step '3/6 Kiểm tra sức khỏe (healthz) của Memory Bridge, Codebase và Dashboard...'
    $bh = Wait-EndpointReady "http://127.0.0.1:$bridgePort/healthz" $HealthTimeoutSec
    $null = Wait-EndpointReady "http://127.0.0.1:$codebasePort/healthz" $HealthTimeoutSec
    $null = Wait-EndpointReady "http://127.0.0.1:$dashPort/healthz" $HealthTimeoutSec
    Write-Host "  [OK] Memory Bridge (schema=$($bh.schema_version)), Codebase, Dashboard đều sẵn sàng." -ForegroundColor Green

    if (!$LegacyOnly) {
        Write-Step 'Hermes: checking actual authenticated Dashboard endpoint...'
        $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
        $hermesReady = $false
        while ((Get-Date) -lt $deadline) {
            try {
                $hs = Invoke-RestMethod -Uri "http://127.0.0.1:$hermesPort/api/status" -TimeoutSec 4
                if ($hs.auth_required -eq $true) { $hermesReady = $true; break }
            } catch {}
            Start-Sleep -Seconds 2
        }
        if (!$hermesReady) { throw '[HERMES_NOT_READY] Inspect docker compose logs --tail 100 hermes-native. Core containers remain intact.' }
        Write-Host "Hermes Dashboard: http://127.0.0.1:$hermesPort (credentials in local .env; model/worker readiness not yet verified)." -ForegroundColor Green
    }

    Write-Step '4/6 Khởi động / xác minh Host Brain Agent (single-instance, không tạo trùng)...'
    & (Join-Path $PSScriptRoot 'start-host-brain-agent.ps1') -Port $hostPort -TimeoutSec 20
    if ($LASTEXITCODE -ne 0) {
        throw "[HOST_AGENT_FAILED] Không thể khởi động Host Brain Agent (exit=$LASTEXITCODE)."
    }

    Write-Step '5/6 Xác minh kết nối thực tế từ Hub (Dashboard container) tới Host Brain Agent...'
    $hubConnected = $false
    $lastStatus = $null
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $lastStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$dashPort/api/status" -TimeoutSec 5
            if ($lastStatus.host_brain -and $lastStatus.host_brain.ready -eq $true) {
                $hubConnected = $true
                break
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    if (!$hubConnected) {
        throw "[HUB_TO_HOST_FAILED] Host Brain Agent chạy cục bộ nhưng Dashboard container không kết nối được (host.docker.internal:$hostPort). Kiểm tra HOST_BRAIN_URL và Docker Desktop networking."
    }
    Write-Host "  [OK] Kết nối thực tế Hub -> Host Brain Agent: READY ($($lastStatus.host_brain.detail))." -ForegroundColor Green

    if ($LegacyOnly -and !$SkipRecoveryScan -and $envMap['BRIDGE_ADMIN_TOKEN']) {
        Write-Step '6/6 Quét phục hồi nhiệm vụ P16B (Hybrid Recovery)...'
        try {
            $hdr = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) }
            $scan = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$bridgePort/v321/recovery/scan" -Headers $hdr -ContentType 'application/json' -Body '{"check_host_runner":true,"apply_auto_policy":true}' -TimeoutSec 20
            Write-Host ("  [OK] P16B Recovery Scan: scanned={0}, auto_resumable={1}, reconnected={2}, pending_review={3}" -f $scan.scanned, $scan.auto_resumable, $scan.reconnected, $scan.pending_review) -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] Không gọi được /v321/recovery/scan (có thể Bridge đang ở schema cũ hơn v15): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    if (!$LegacyOnly) {
        Write-Warning 'Hermes pilot: legacy automatic recovery suppressed; use isolated synthetic boards only. No CLI worker adapter or production cutover yet.'
    }
    Write-Host "`nAI Memory Hub đã khởi động đầy đủ và sẵn sàng tại: http://127.0.0.1:$dashPort" -ForegroundColor Green
} finally {
    Pop-Location
}
