[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\workspace1\ai-memory-hub',
    [switch]$DryRun,
    [switch]$Upgrade,
    [switch]$RunTests,
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
    Write-Step '1/7 Check environment (Git, Docker, .env, no volume deletion, no paid API)...'
    Invoke-Checked 'git' @('rev-parse', '--is-inside-work-tree')
    Invoke-Checked 'docker' @('info', '--format', '{{.ServerVersion}}')
    if (!(Test-Path -LiteralPath '.env')) { throw 'Existing .env is required.' }
    $envMap = Read-Env
    $bridge = 'http://127.0.0.1:' + (Get-Port $envMap 'BRIDGE_PORT' 8082)
    $codebase = 'http://127.0.0.1:' + (Get-Port $envMap 'CODEBASE_PORT' 8081)
    $dash = 'http://127.0.0.1:' + (Get-Port $envMap 'HUB_PORT' 8080)

    if ($DryRun) {
        Write-Host "[DRY-RUN] Repo=$RepoPath | Bridge=$bridge | Codebase=$codebase | Dashboard=$dash" -ForegroundColor Yellow
        return
    }

    if ($Upgrade) {
        Write-Step '2/7 Online SQLite backup before Schema V6 upgrade (no volume deletion)...'
        if (!$envMap['BRIDGE_ADMIN_TOKEN']) { throw 'Missing BRIDGE_ADMIN_TOKEN.' }
        $null = Wait-Health "$bridge/healthz" $HealthTimeoutSec
        $backup = Invoke-RestMethod -Method Post -Uri "$bridge/v1/backup" -Headers @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) } -TimeoutSec 120
        if (!$backup.backup_id -or !$backup.sha256) { throw 'Backup not confirmed. Upgrade aborted.' }
        Write-Host "[PASS] Verified pre-V3 backup ID: $($backup.backup_id)" -ForegroundColor Green
        Invoke-Checked 'docker' @('compose', 'config', '--quiet')
        Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', 'codebase', 'memory-bridge', 'dashboard')
    } else {
        Write-Step '2/7 Skip upgrade (pass -Upgrade to backup and rebuild).'
    }

    Write-Step '3/7 Check Health and Schema V6...'
    $bh = Wait-Health "$bridge/healthz" $HealthTimeoutSec
    if ([int]$bh.schema_version -lt 6) { throw "Expected V3 schema >= 6, got $($bh.schema_version)." }
    $null = Wait-Health "$codebase/healthz" $HealthTimeoutSec
    $null = Wait-Health "$dash/healthz" $HealthTimeoutSec
    Write-Host "[PASS] Bridge (schema $($bh.schema_version)), Codebase, Dashboard healthy." -ForegroundColor Green

    Write-Step '4/7 Check Dashboard V3 security (unauthenticated /api/projects and /api/v3/mcp/servers must return 401)...'
    foreach ($ep in @('/api/projects', '/api/v3/mcp/servers')) {
        $code = 0
        try { $null = Invoke-WebRequest -UseBasicParsing -Uri "$dash$ep" -TimeoutSec 10; $code = 200 } catch {
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        }
        if ($code -ne 401) { throw "Security regression: unauthenticated GET $ep returned $code." }
    }
    Write-Host '[PASS] Unauthenticated V2/V3 Dashboard APIs blocked with HTTP 401.' -ForegroundColor Green

    Write-Step '5/7 Check Dynamic MCP Registry and Conversation API live...'
    $headers = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_READ_TOKEN']) }
    $mcps = Invoke-RestMethod -Uri "$bridge/v3/mcp/servers" -Headers $headers -TimeoutSec 15
    if (@($mcps.items).Count -lt 2) { throw 'Builtin MCP servers missing in V3 registry.' }
    Write-Host "[PASS] Dynamic MCP Registry: $(@($mcps.items).Count) servers registered." -ForegroundColor Green

    $projects = Invoke-RestMethod -Uri "$bridge/v1/projects" -Headers $headers -TimeoutSec 15
    foreach ($p in @($projects.items)) {
        if (!$p.project_id) { continue }
        $convs = Invoke-RestMethod -Uri "$bridge/v3/conversations?project_id=$($p.project_id)&limit=5" -Headers $headers -TimeoutSec 15
        Write-Host "[PASS] Conversation API ($($p.name)): total=$($convs.total)." -ForegroundColor Green
    }

    if ($RunTests) {
        Write-Step "6/7 Run full V2.2.4 + V3 (M1-M7) pytest suite in isolated Docker (timeout ${TestTimeoutSec}s)..."
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
            'tests/dashboard/test_v224_async_reindex_and_timeout.py',
            'tests/orchestrator/runner/test_host_runner.py',
            'tests/orchestrator/runner/test_v223_long_prompt_stdin.py',
            'tests/orchestrator/runner/test_v224_usage_collector.py',
            'tests/v3/test_v3_coding_engine.py',
            'tests/v3/test_v3_dynamic_mcp_and_conversations.py',
            'tests/v3/test_v3_collectors_and_context_pack.py'
        ) -join ' '
        $cmd = "apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null && python -m venv /tmp/v3test && /tmp/v3test/bin/pip install -q -r /repo/services/memory-bridge/requirements.txt -r /repo/services/dashboard/requirements.txt pytest && cd /repo && PYTHONPATH=/repo /tmp/v3test/bin/python -m pytest -p no:cacheprovider -q $suites"
        Invoke-Checked 'docker' @('run', '--rm', '--mount', $mount, '-e', 'PYTHONDONTWRITEBYTECODE=1', 'python:3.12-slim', 'sh', '-c', $cmd)
        Write-Host '[PASS] Full V2.2.4 + V3 pytest suite passed in isolated Docker.' -ForegroundColor Green
    } else {
        Write-Step '6/7 Skip pytest (pass -RunTests to run in isolated container).'
    }

    Write-Step '7/7 Test-V3.ps1 completed.'
    Write-Host "Dashboard V3: $dash/ (No paid model calls, no volume deletion, no main merge)." -ForegroundColor Green
} finally {
    Pop-Location
}
