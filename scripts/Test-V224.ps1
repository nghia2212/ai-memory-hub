[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\workspace1\ai-memory-hub',
    [switch]$DryRun,
    [switch]$Upgrade,
    [switch]$RunTests,
    [switch]$BrowserTests,
    [int]$HealthTimeoutSec = 120,
    [int]$TestTimeoutSec = 360
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
    Write-Step '1/8 Kiểm tra môi trường (Git, Docker, .env, không xóa volume, không gọi model trả phí)...'
    Invoke-Checked 'git' @('rev-parse', '--is-inside-work-tree')
    Invoke-Checked 'docker' @('info', '--format', '{{.ServerVersion}}')
    if (!(Test-Path -LiteralPath '.env')) { throw 'Existing .env is required.' }
    $envMap = Read-Env
    $bridge = 'http://127.0.0.1:' + (Get-Port $envMap 'BRIDGE_PORT' 8082)
    $codebase = 'http://127.0.0.1:' + (Get-Port $envMap 'CODEBASE_PORT' 8081)
    $dash = 'http://127.0.0.1:' + (Get-Port $envMap 'HUB_PORT' 8080)

    if ($DryRun) {
        Write-Host "[DRY-RUN] Repo=$RepoPath | Bridge=$bridge | Codebase=$codebase | Dashboard=$dash" -ForegroundColor Yellow
        Write-Host "[DRY-RUN] Upgrade=$Upgrade | RunTests=$RunTests | BrowserTests=$BrowserTests" -ForegroundColor Yellow
        return
    }

    if ($Upgrade) {
        Write-Step '2/8 Backup SQLite trực tuyến trước khi nâng cấp...'
        if (!$envMap['BRIDGE_ADMIN_TOKEN']) { throw 'Missing BRIDGE_ADMIN_TOKEN.' }
        $null = Wait-Health "$bridge/healthz" $HealthTimeoutSec
        $backup = Invoke-RestMethod -Method Post -Uri "$bridge/v1/backup" -Headers @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) } -TimeoutSec 120
        if (!$backup.backup_id -or !$backup.sha256) { throw 'Backup not confirmed. Upgrade aborted.' }
        Write-Host "[PASS] Verified backup ID: $($backup.backup_id)" -ForegroundColor Green
        Invoke-Checked 'docker' @('compose', 'config', '--quiet')
        Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', 'codebase', 'memory-bridge', 'dashboard')
    } else {
        Write-Step '2/8 Bỏ qua nâng cấp (thêm -Upgrade để backup & rebuild).'
    }

    Write-Step '3/8 Kiểm tra Health Check & Schema...'
    $bh = Wait-Health "$bridge/healthz" $HealthTimeoutSec
    if ([int]$bh.schema_version -lt 4) { throw "Schema version $($bh.schema_version) < 4." }
    $null = Wait-Health "$codebase/healthz" $HealthTimeoutSec
    $null = Wait-Health "$dash/healthz" $HealthTimeoutSec
    Write-Host "[PASS] Bridge (schema $($bh.schema_version)), Codebase, Dashboard healthy." -ForegroundColor Green

    Write-Step '4/8 Kiểm tra bảo mật Dashboard (/api/projects phải trả 401 khi chưa đăng nhập)...'
    $unauth = 0
    try { $null = Invoke-WebRequest -UseBasicParsing -Uri "$dash/api/projects" -TimeoutSec 10; $unauth = 200 } catch {
        if ($_.Exception.Response) { $unauth = [int]$_.Exception.Response.StatusCode }
    }
    if ($unauth -ne 401) { throw "Security regression: unauthenticated /api/projects returned $unauth." }
    Write-Host '[PASS] Unauthenticated /api/projects blocked with HTTP 401.' -ForegroundColor Green

    Write-Step '5/8 Kiểm tra Trace API V2.2.4 (summary, groups, execution_sequence, telemetry inspector)...'
    $headers = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_READ_TOKEN']) }
    $projects = Invoke-RestMethod -Uri "$bridge/v1/projects" -Headers $headers -TimeoutSec 15
    foreach ($p in @($projects.items)) {
        if (!$p.project_id) { continue }
        $r = Invoke-RestMethod -Uri "$bridge/v2/traces?project_id=$($p.project_id)&limit=5" -Headers $headers -TimeoutSec 15
        if ($null -eq $r.summary -or $null -eq $r.groups) { throw "V2.2.4 summary/groups missing for $($p.project_id)." }
        $insp = Invoke-RestMethod -Uri "$bridge/v2/traces/telemetry/inspector?project_id=$($p.project_id)" -Headers $headers -TimeoutSec 15
        if ($null -eq $insp.client_capabilities) { throw "Telemetry inspector missing for $($p.project_id)." }
        Write-Host "[PASS] V2.2.4 Trace & Telemetry Inspector ($($p.name)): total=$($r.total), lanes=$($r.groups.Count)." -ForegroundColor Green
    }

    Write-Step '6/8 Kiểm tra Usage Collector (read-only dry status)...'
    $uc = (& python services/orchestrator/usage_collector.py) | ConvertFrom-Json
    if ($uc.status -ne 'ready') { throw 'Usage collector not ready.' }
    Write-Host "[PASS] Usage Collector ready: $($uc.supported_clients -join ', ')." -ForegroundColor Green

    if ($RunTests) {
        Write-Step "7/8 Chạy toàn bộ bộ pytest V2.2.3 + V2.2.4 trong Docker cô lập (timeout ${TestTimeoutSec}s)..."
        $root = (Get-Location).Path
        $mount = "type=bind,source=$root,target=/repo,readonly"
        $suites = @(
            'tests/bridge/test_bridge_core.py',
            'tests/bridge/test_mcp_handshake.py',
            'tests/bridge/test_v2_bridge.py',
            'tests/bridge/test_v21_lease_recovery.py',
            'tests/bridge/test_v22_traces.py',
            'tests/bridge/test_v221_quota.py',
            'tests/bridge/test_v222_long_prompt.py',
            'tests/bridge/test_v223_brain_and_quota.py',
            'tests/bridge/test_v224_execution_summary_and_telemetry.py',
            'tests/dashboard/test_dashboard.py',
            'tests/dashboard/test_v2_dashboard.py',
            'tests/dashboard/test_v223_auth_security.py',
            'tests/orchestrator/runner/test_host_runner.py',
            'tests/orchestrator/runner/test_v223_long_prompt_stdin.py',
            'tests/orchestrator/runner/test_v224_usage_collector.py'
        ) -join ' '
        $cmd = "python -m venv /tmp/v224test && /tmp/v224test/bin/pip install -q -r /repo/services/memory-bridge/requirements.txt -r /repo/services/dashboard/requirements.txt pytest && cd /repo && PYTHONPATH=/repo /tmp/v224test/bin/python -m pytest -p no:cacheprovider -q $suites"
        Invoke-Checked 'docker' @('run', '--rm', '--mount', $mount, '-e', 'PYTHONDONTWRITEBYTECODE=1', 'python:3.12-slim', 'sh', '-c', $cmd)
        Write-Host '[PASS] Toàn bộ backend/unit/regression pytest V2.2.4 đã vượt qua.' -ForegroundColor Green
    } else {
        Write-Step '7/8 Bỏ qua pytest (thêm -RunTests để chạy trong container cô lập).'
    }

    if ($BrowserTests) {
        Write-Step '8/8 Chạy Headless Playwright Browser QA (100/500/1000 traces, 1366x768 & 1920x1080)...'
        $root = (Get-Location).Path
        $mount = "type=bind,source=$root,target=/repo,readonly"
        $cmd = "pip install -q playwright==1.50.0 -r /repo/services/memory-bridge/requirements.txt -r /repo/services/dashboard/requirements.txt && cd /repo && PYTHONPATH=/repo python tests/dashboard/browser_v224_qa.py"
        Invoke-Checked 'docker' @('run', '--rm', '--mount', $mount, '-e', 'PYTHONDONTWRITEBYTECODE=1', 'mcr.microsoft.com/playwright/python:v1.50.0-noble', 'sh', '-c', $cmd)
        Write-Host '[PASS] Headless Playwright Browser QA đã vượt qua.' -ForegroundColor Green
    } else {
        Write-Step '8/8 Bỏ qua Browser QA (thêm -BrowserTests để chạy Playwright cô lập).'
    }

    Write-Host "Hoàn tất Test-V224.ps1. Dashboard: $dash/ (Không gọi model trả phí, không xóa volume)." -ForegroundColor Green
} finally {
    Pop-Location
}
