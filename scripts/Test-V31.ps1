[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\workspace1\ai-memory-hub',
    [switch]$DryRun,
    [switch]$CheckEnvironment,
    [switch]$Backup,
    [switch]$Upgrade,
    [switch]$RunTests,
    [switch]$BrowserTests,
    [switch]$McpHealth,
    [switch]$DiscoverTools,
    [switch]$VerifyBusinessWorkspace,
    [ValidateSet('Shared','Game','Architect','Developer','Custom','All')]
    [string]$SelectPack = 'Shared',
    [int]$HealthTimeoutSec = 120,
    [int]$TestTimeoutSec = 420
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

if (!(Test-Path -LiteralPath $RepoPath -PathType Container)) { throw "Repository folder not found: $RepoPath" }
Push-Location -LiteralPath $RepoPath
try {
    Write-Step '1/9 Environment & Toolchain Check (no volume deletion, no git reset, no secrets printed)...'
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    $dirty = (& git status --porcelain)
    Write-Host "  Git branch: $branch | Dirty files: $(@($dirty).Count)" -ForegroundColor Gray
    Invoke-Checked 'docker' @('info', '--format', '{{.ServerVersion}}')

    foreach ($tool in @('python','node','npm','java','mvn','gradle','git','docker','ffmpeg','adb','claude','codex','gemini','agy')) {
        $found = Get-Command $tool -ErrorAction SilentlyContinue
        $state = if ($found) { "FOUND ($($found.Source))" } else { 'NOT_FOUND' }
        Write-Host ("  Tool {0,-10}: {1}" -f $tool, $state) -ForegroundColor DarkGray
    }
    $blender = Get-Command blender -ErrorAction SilentlyContinue
    if (!$blender) {
        $bAlt = Get-ChildItem 'C:\Program Files\Blender Foundation\*\blender.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        Write-Host ("  Tool {0,-10}: {1}" -f 'blender', $(if ($bAlt) { "FOUND ($($bAlt.FullName))" } else { 'NOT_FOUND' })) -ForegroundColor DarkGray
    } else {
        Write-Host ("  Tool {0,-10}: FOUND ({1})" -f 'blender', $blender.Source) -ForegroundColor DarkGray
    }

    if (!(Test-Path -LiteralPath '.env')) { throw 'Existing .env is required (never committed to Git).' }
    $envMap = Read-Env
    $bridge = 'http://127.0.0.1:' + (Get-Port $envMap 'BRIDGE_PORT' 8082)
    $codebase = 'http://127.0.0.1:' + (Get-Port $envMap 'CODEBASE_PORT' 8081)
    $dash = 'http://127.0.0.1:' + (Get-Port $envMap 'HUB_PORT' 8080)

    if ($DryRun -or $CheckEnvironment) {
        Write-Host "[INFO] Mode=DryRun/CheckEnvironment | Pack=$SelectPack | Bridge=$bridge | Codebase=$codebase | Dashboard=$dash" -ForegroundColor Yellow
        if ($DryRun) { return }
    }

    if ($Backup -or $Upgrade) {
        Write-Step '2/9 Online SQLite Backup before Schema V8 upgrade (preserves all V3 data & volumes)...'
        if (!$envMap['BRIDGE_ADMIN_TOKEN']) { throw 'Missing BRIDGE_ADMIN_TOKEN.' }
        $null = Wait-Health "$bridge/healthz" $HealthTimeoutSec
        $bk = Invoke-RestMethod -Method Post -Uri "$bridge/v1/backup" -Headers @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) } -TimeoutSec 120
        if (!$bk.backup_id -or !$bk.sha256) { throw 'Backup not confirmed. Aborting upgrade.' }
        Write-Host "[PASS] Verified pre-V3.1 backup ID: $($bk.backup_id) (schema=$($bk.schema_version))" -ForegroundColor Green
    }

    if ($Upgrade) {
        Write-Step '3/9 Rebuilding and starting Docker services (codebase, memory-bridge, dashboard)...'
        Invoke-Checked 'docker' @('compose', 'config', '--quiet')
        Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', 'codebase', 'memory-bridge', 'dashboard')
    } else {
        Write-Step '3/9 Skip rebuild (pass -Upgrade to backup and rebuild containers).'
    }

    Write-Step '4/9 Checking service health & Schema V8...'
    $bh = Wait-Health "$bridge/healthz" $HealthTimeoutSec
    $null = Wait-Health "$codebase/healthz" $HealthTimeoutSec
    $null = Wait-Health "$dash/healthz" $HealthTimeoutSec
    Write-Host "[PASS] Bridge (schema=$($bh.schema_version)), Codebase, Dashboard healthy." -ForegroundColor Green

    Write-Step '5/9 Verifying Dashboard V3.1 Auth/CSRF boundary (unauthenticated must return 401)...'
    foreach ($ep in @('/api/projects', '/api/v3/mcp/servers', '/api/v31/businesses', '/api/v31/mcp/servers', '/api/v31/mcp/monitoring')) {
        $code = 0
        try { $null = Invoke-WebRequest -UseBasicParsing -Uri "$dash$ep" -TimeoutSec 10; $code = 200 } catch {
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        }
        if ($code -ne 401) { throw "Security regression: unauthenticated GET $ep returned HTTP $code." }
    }
    Write-Host '[PASS] All V1/V2/V3/V3.1 Dashboard APIs enforce HTTP 401 when unauthenticated.' -ForegroundColor Green

    $readHdr = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_READ_TOKEN']) }
    $writeHdr = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_WRITE_TOKEN']) }
    $adminHdr = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) }

    if ($VerifyBusinessWorkspace -and [int]$bh.schema_version -ge 8) {
        Write-Step '6/9 Verifying Business Workspaces live API...'
        $biz = Invoke-RestMethod -Uri "$bridge/v31/businesses" -Headers $readHdr -TimeoutSec 15
        Write-Host "[PASS] Business Workspaces active: $(@($biz.items).Count)" -ForegroundColor Green
    }

    if (($McpHealth -or $DiscoverTools) -and [int]$bh.schema_version -ge 8) {
        Write-Step '7/9 Running real MCP Health Check / Tool Discovery...'
        $srvs = Invoke-RestMethod -Uri "$bridge/v31/mcp/servers" -Headers $readHdr -TimeoutSec 15
        foreach ($s in @($srvs.items)) {
            if ($McpHealth) {
                $hc = Invoke-RestMethod -Method Post -Uri "$bridge/v31/mcp/servers/$($s.id)/health" -Headers $writeHdr -TimeoutSec 20
                Write-Host "  Health $($s.name): $($hc.health_status) ($($hc.latency_ms)ms)" -ForegroundColor Gray
            }
            if ($DiscoverTools -and $s.approval_state -eq 'approved') {
                try {
                    $dc = Invoke-RestMethod -Method Post -Uri "$bridge/v31/mcp/servers/$($s.id)/discover" -Headers $adminHdr -TimeoutSec 25
                    Write-Host "  Discover $($s.name): $($dc.discovered_count) tools" -ForegroundColor Gray
                } catch {
                    Write-Host "  Discover $($s.name): skipped/error ($($_.Exception.Message))" -ForegroundColor Yellow
                }
            }
        }
    }

    if ($RunTests) {
        Write-Step "8/9 Running full V2.2.4 + V3 + V3.1 pytest regression suite in isolated Docker (timeout ${TestTimeoutSec}s)..."
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
            'tests/v3/test_v3_collectors_and_context_pack.py',
            'tests/v31/test_v31_p0_foundation.py',
            'tests/v31/test_v31_business_workspace.py',
            'tests/v31/test_v31_mcp_gateway_and_packs.py'
        ) -join ' '
        $cmd = "apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null && python -m venv /tmp/v31test && /tmp/v31test/bin/pip install -q -r /repo/services/memory-bridge/requirements.txt -r /repo/services/dashboard/requirements.txt pytest && cd /repo && PYTHONPATH=/repo /tmp/v31test/bin/python -m pytest -p no:cacheprovider -q $suites"
        Invoke-Checked 'docker' @('run', '--rm', '--mount', $mount, '-e', 'PYTHONDONTWRITEBYTECODE=1', 'python:3.12-slim', 'sh', '-c', $cmd)
        Write-Host '[PASS] Full V2.2.4 + V3 + V3.1 pytest suite passed.' -ForegroundColor Green
    }

    if ($BrowserTests) {
        Write-Step '8b/9 Browser Acceptance check note: run Playwright against Dashboard at 1366x768 & 1920x1080.'
    }

    Write-Step '9/9 Test-V31.ps1 completed cleanly.'
    Write-Host "Dashboard V3.1: $dash/ (No paid API calls, no volume deletion, no main merge)." -ForegroundColor Green
} finally {
    Pop-Location
}
