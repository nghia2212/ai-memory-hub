[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\workspace1\ai-memory-hub',
    [switch]$DryRun,
    [switch]$Upgrade,
    [switch]$RunTests,
    [int]$HealthTimeoutSec = 120,
    [int]$TestTimeoutSec = 420
)
$ErrorActionPreference = 'Stop'
$script:Summary = @()

function Record-Gate([string]$Gate, [string]$Status, [string]$Detail) {
    $script:Summary += [pscustomobject]@{ Gate = $Gate; Status = $Status; Detail = $Detail }
    $color = switch ($Status) {
        'PASS' { 'Green' }
        'FAIL' { 'Red' }
        'BLOCKED' { 'Yellow' }
        default { 'DarkGray' }
    }
    Write-Host ("  [{0,-7}] {1}: {2}" -f $Status, $Gate, $Detail) -ForegroundColor $color
}

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

if (!(Test-Path -LiteralPath $RepoPath -PathType Container)) { throw "Repository folder not found: $RepoPath" }
Push-Location -LiteralPath $RepoPath
try {
    Write-Step '1/8 Read-Only Environment & Docker Check (no volume deletion, no DB reset, no paid API, no auto-restart)...'
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    $headSha = (& git rev-parse --short HEAD).Trim()
    $dockerVer = (& docker info --format '{{.ServerVersion}}').Trim()
    Record-Gate 'Git & Docker' 'PASS' "Branch=$branch ($headSha), Docker=$dockerVer"

    if (!(Test-Path -LiteralPath '.env')) {
        Record-Gate 'Config (.env)' 'BLOCKED' 'Missing .env file'
        throw 'Existing .env is required.'
    }
    $envMap = Read-Env
    $bridge = 'http://127.0.0.1:' + $(if ($envMap['BRIDGE_PORT']) { $envMap['BRIDGE_PORT'] } else { '8082' })
    $codebase = 'http://127.0.0.1:' + $(if ($envMap['CODEBASE_PORT']) { $envMap['CODEBASE_PORT'] } else { '8081' })
    $dash = 'http://127.0.0.1:' + $(if ($envMap['HUB_PORT']) { $envMap['HUB_PORT'] } else { '8080' })

    if ($DryRun) {
        Record-Gate 'DryRun' 'SKIP' "Bridge=$bridge | Codebase=$codebase | Dashboard=$dash (no changes performed)"
        return
    }

    if ($Upgrade) {
        Write-Step '2/8 Online SQLite Backup & Safe Rebuild (preserves all volumes & V1-V3.1 data)...'
        $null = Wait-Health "$bridge/healthz" $HealthTimeoutSec
        $bk = Invoke-RestMethod -Method Post -Uri "$bridge/v1/backup" -Headers @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) } -TimeoutSec 120
        if (!$bk.backup_id -or !$bk.sha256) { throw 'Online backup not confirmed. Upgrade aborted.' }
        Record-Gate 'Pre-Upgrade Backup' 'PASS' "backup_id=$($bk.backup_id) schema=$($bk.schema_version)"
        Invoke-Checked 'docker' @('compose', 'config', '--quiet')
        Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', 'codebase', 'memory-bridge', 'dashboard')
        Record-Gate 'Docker Upgrade' 'PASS' 'Rebuilt & restarted codebase, memory-bridge, dashboard cleanly'
    } else {
        Record-Gate 'Upgrade' 'SKIP' 'Default read-only mode (pass -Upgrade to backup & rebuild)'
    }

    Write-Step '3/8 Checking Service Health, Schema V9 & Host Runner...'
    $bh = Wait-Health "$bridge/healthz" $HealthTimeoutSec
    $null = Wait-Health "$codebase/healthz" $HealthTimeoutSec
    $null = Wait-Health "$dash/healthz" $HealthTimeoutSec
    $schemaStatus = if ([int]$bh.schema_version -ge 9) { 'PASS' } else { 'FAIL' }
    Record-Gate 'Health & Schema' $schemaStatus "Bridge schema=$($bh.schema_version), Codebase=ready, Dashboard=ready"
    if ($schemaStatus -eq 'FAIL') { throw "Expected schema >= 9, got $($bh.schema_version)." }

    try {
        $hr = Invoke-RestMethod -Uri 'http://127.0.0.1:8084/healthz' -TimeoutSec 3
        Record-Gate 'Host Runner' 'PASS' "Host Brain Agent ready=$($hr.ready)"
    } catch {
        Record-Gate 'Host Runner' 'SKIP' 'Host Brain Agent (:8084) not running (optional on-demand)'
    }

    Write-Step '4/8 Verifying RBAC / Auth 401 Boundary on V3.2 & P6A Dashboard APIs...'
    $authOk = $true
    foreach ($ep in @('/api/v31/businesses', '/api/v32/skills', '/api/v32/bindings', '/api/v2/traces/scope-options?project_id=20ef268d-5a07-43a7-a66f-8a83e87dd0f7')) {
        $code = 0
        try { $null = Invoke-WebRequest -UseBasicParsing -Uri "$dash$ep" -TimeoutSec 10; $code = 200 } catch {
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        }
        if ($code -ne 401) { $authOk = $false; Record-Gate "Auth $ep" 'FAIL' "Expected 401, got $code" }
    }
    if ($authOk) { Record-Gate 'Dashboard Auth (401)' 'PASS' 'All V3.1/V3.2/P6A endpoints block unauthenticated requests' } else { throw 'Auth boundary failed.' }

    $readHdr = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_READ_TOKEN']) }
    Write-Step '5/8 Read-Only Check: Skill Registry, MCP Connectivity & P6A Scope Options...'
    $sks = Invoke-RestMethod -Uri "$bridge/v32/skills" -Headers $readHdr -TimeoutSec 15
    Record-Gate 'Skill Registry' $(if ($sks.total -ge 30) { 'PASS' } else { 'FAIL' }) "Active skills=$($sks.total)"

    $mcps = Invoke-RestMethod -Uri "$bridge/v31/mcp/servers" -Headers $readHdr -TimeoutSec 15
    $readyMcps = @($mcps.items | Where-Object { $_.health_status -eq 'ready' }).Count
    Record-Gate 'MCP Registry' $(if ($readyMcps -ge 2) { 'PASS' } else { 'BLOCKED' }) "Servers=$(@($mcps.items).Count), ready=$readyMcps"

    $projs = Invoke-RestMethod -Uri "$bridge/v1/projects" -Headers $readHdr -TimeoutSec 15
    $p6aDetails = @()
    foreach ($p in @($projs.items)) {
        $opts = Invoke-RestMethod -Uri "$bridge/v2/traces/scope-options?project_id=$($p.project_id)" -Headers $readHdr -TimeoutSec 15
        $p6aDetails += "$($p.name)(plans=$(@($opts.plans).Count),tasks=$(@($opts.tasks).Count),attempts=$(@($opts.attempts).Count))"
    }
    Record-Gate 'P6A Scope Options' 'PASS' ($p6aDetails -join '; ')

    if ($RunTests) {
        Write-Step "6/8 Running Isolated Docker Pytest Suite (V2.2.4 + V3 + V3.1 + V3.2 & P6A, timeout ${TestTimeoutSec}s)..."
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
            'tests/v31/test_v31_mcp_gateway_and_packs.py',
            'tests/v32/test_v32_skills_and_resolution.py',
            'tests/v32/test_v32_p6a_task_centric_execution_map.py'
        ) -join ' '
        $cmd = "apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null && python -m venv /tmp/v32test && /tmp/v32test/bin/pip install -q -r /repo/services/memory-bridge/requirements.txt -r /repo/services/dashboard/requirements.txt pytest && cd /repo && PYTHONPATH=/repo /tmp/v32test/bin/python -m pytest -p no:cacheprovider -q $suites"
        Invoke-Checked 'docker' @('run', '--rm', '--mount', $mount, '-e', 'PYTHONDONTWRITEBYTECODE=1', 'python:3.12-slim', 'sh', '-c', $cmd)
        Record-Gate 'Isolated Pytest Suite' 'PASS' 'All V2.2.4 + V3 + V3.1 + V3.2 (SK32 & P6A) suites passed'
    } else {
        Record-Gate 'Isolated Pytest Suite' 'SKIP' 'Pass -RunTests to run full isolated Docker pytest suite'
    }

    Write-Step '7/8 Acceptance Summary Table:'
    $script:Summary | Format-Table -AutoSize
    Write-Step "8/8 Test-V32.ps1 completed cleanly. Dashboard: $dash/"
} finally {
    Pop-Location
}
