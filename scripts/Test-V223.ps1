[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\workspace1\ai-memory-hub',
    [switch]$Upgrade,
    [switch]$RunTests,
    [int]$HealthTimeoutSec = 120,
    [int]$TestTimeoutSec = 300
)
$ErrorActionPreference = 'Stop'

function Write-Step([string]$Msg) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $Msg) -ForegroundColor Cyan
}
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed (exit $LASTEXITCODE)." }
}
function Read-Env {
    $map = @{}
    foreach ($line in Get-Content -LiteralPath '.env' -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $map[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    return $map
}
function Wait-Health([string]$Url, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $h = Invoke-RestMethod -Uri $Url -TimeoutSec 4
            if ($h.ready -eq $true) { return $h }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "Health check timeout (${TimeoutSec}s): $Url"
}
function Get-Port($Map, [string]$Key, [int]$Default) {
    if ($Map[$Key]) { return [int]$Map[$Key] }
    return $Default
}

if (!(Test-Path -LiteralPath $RepoPath -PathType Container)) { throw 'Repository folder not found. Set -RepoPath.' }
Push-Location -LiteralPath $RepoPath
try {
    Write-Step '1/6 Kiểm tra Git, Docker và .env...'
    Invoke-Checked 'git' @('rev-parse', '--is-inside-work-tree')
    Invoke-Checked 'docker' @('info', '--format', '{{.ServerVersion}}')
    if (!(Test-Path -LiteralPath '.env')) { throw 'Existing .env is required.' }
    $envMap = Read-Env
    $bridge = 'http://127.0.0.1:' + (Get-Port $envMap 'BRIDGE_PORT' 8082)
    $codebase = 'http://127.0.0.1:' + (Get-Port $envMap 'CODEBASE_PORT' 8081)
    $dash = 'http://127.0.0.1:' + (Get-Port $envMap 'HUB_PORT' 8080)

    if ($Upgrade) {
        Write-Step '2/6 Sao lưu SQLite trực tuyến trước khi nâng cấp (không xóa volume)...'
        if (!$envMap['BRIDGE_ADMIN_TOKEN']) { throw 'Missing BRIDGE_ADMIN_TOKEN.' }
        $null = Wait-Health "$bridge/healthz" $HealthTimeoutSec
        $backup = Invoke-RestMethod -Method Post -Uri "$bridge/v1/backup" -Headers @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) } -TimeoutSec 120
        if (!$backup.backup_id -or !$backup.sha256) { throw 'Backup not confirmed. Upgrade aborted.' }
        Write-Host "[PASS] Verified backup ID: $($backup.backup_id)" -ForegroundColor Green
        Invoke-Checked 'docker' @('compose', 'config', '--quiet')
        Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', 'codebase', 'memory-bridge', 'dashboard')
    } else {
        Write-Step '2/6 Chế độ kiểm thử không nâng cấp (bỏ qua rebuild; thêm -Upgrade để backup & rebuild).'
    }

    Write-Step '3/6 Kiểm tra Health Check 3 dịch vụ lõi...'
    $bh = Wait-Health "$bridge/healthz" $HealthTimeoutSec
    if ([int]$bh.schema_version -lt 4) { throw 'Schema version < 4.' }
    $null = Wait-Health "$codebase/healthz" $HealthTimeoutSec
    $null = Wait-Health "$dash/healthz" $HealthTimeoutSec
    Write-Host "[PASS] Bridge (schema $($bh.schema_version)), Codebase, Dashboard healthy." -ForegroundColor Green

    Write-Step '4/6 Kiểm tra bảo mật Dashboard (/api/projects phải trả 401 khi chưa đăng nhập)...'
    $unauthStatus = 0
    try {
        $null = Invoke-WebRequest -UseBasicParsing -Uri "$dash/api/projects" -TimeoutSec 10
        $unauthStatus = 200
    } catch {
        if ($_.Exception.Response) { $unauthStatus = [int]$_.Exception.Response.StatusCode }
    }
    if ($unauthStatus -ne 401) { throw "Security regression: GET /api/projects without login returned $unauthStatus (expected 401)." }
    Write-Host '[PASS] Unauthenticated GET /api/projects blocked with HTTP 401.' -ForegroundColor Green

    Write-Step '5/6 Kiểm tra Memory Bridge & Trace API...'
    if (!$envMap['BRIDGE_READ_TOKEN']) { throw 'Missing BRIDGE_READ_TOKEN.' }
    $headers = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_READ_TOKEN']) }
    $projects = Invoke-RestMethod -Uri "$bridge/v1/projects" -Headers $headers -TimeoutSec 15
    foreach ($p in @($projects.items)) {
        if (!$p.project_id) { continue }
        $r = Invoke-RestMethod -Uri "$bridge/v2/traces?project_id=$($p.project_id)&limit=1" -Headers $headers -TimeoutSec 15
        if ($null -eq $r.metrics -or $null -eq $r.total) { throw 'Invalid trace response.' }
        Write-Host "[PASS] Trace API ($($p.name)): $($r.total) observed steps." -ForegroundColor Green
    }

    if ($RunTests) {
        Write-Step "6/6 Chạy bộ pytest V2.2 + V2.2.3 trong container cô lập (timeout ${TestTimeoutSec}s)..."
        $root = (Get-Location).Path
        $mount = "type=bind,source=$root,target=/repo,readonly"
        $suites = @(
            'tests/bridge/test_bridge_core.py tests/bridge/test_mcp_handshake.py tests/bridge/test_v2_bridge.py tests/bridge/test_v21_lease_recovery.py tests/bridge/test_v22_traces.py tests/bridge/test_v221_quota.py tests/bridge/test_v222_long_prompt.py tests/bridge/test_v223_brain_and_quota.py',
            'tests/dashboard/test_dashboard.py tests/dashboard/test_v2_dashboard.py tests/dashboard/test_v223_auth_security.py',
            'tests/orchestrator/runner/test_host_runner.py tests/orchestrator/runner/test_v223_long_prompt_stdin.py'
        )
        $cmd = "python -m venv /tmp/v223test && /tmp/v223test/bin/pip install -q -r /repo/services/memory-bridge/requirements.txt -r /repo/services/dashboard/requirements.txt pytest && cd /repo && PYTHONPATH=/repo /tmp/v223test/bin/python -m pytest -p no:cacheprovider -q " + ($suites -join ' ')
        Invoke-Checked 'docker' @('run', '--rm', '--mount', $mount, '-e', 'PYTHONDONTWRITEBYTECODE=1', 'python:3.12-slim', 'sh', '-c', $cmd)
        Write-Host '[PASS] Toàn bộ bộ kiểm thử V2.2.3 đã vượt qua trong container cô lập.' -ForegroundColor Green
    } else {
        Write-Step '6/6 Bỏ qua pytest (thêm -RunTests để chạy toàn bộ suite trong Docker cô lập).'
    }

    Write-Host "Hoàn tất Test-V223.ps1. Dashboard: $dash/ (Không gọi model trả phí, không xóa volume)." -ForegroundColor Green
} finally {
    Pop-Location
}
