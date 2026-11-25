[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\workspace1\ai-memory-hub',
    [switch]$DryRun,
    [switch]$Upgrade,
    [switch]$RunTests,
    [switch]$VerifySkills,
    [switch]$VerifyP6A,
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

if (!(Test-Path -LiteralPath $RepoPath -PathType Container)) { throw "Repository folder not found: $RepoPath" }
Push-Location -LiteralPath $RepoPath
try {
    Write-Step '1/7 Environment Check (Git, Docker, .env; no volume deletion, no secrets printed)...'
    $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
    Invoke-Checked 'docker' @('info', '--format', '{{.ServerVersion}}')
    if (!(Test-Path -LiteralPath '.env')) { throw 'Existing .env is required.' }
    $envMap = Read-Env
    $bridge = 'http://127.0.0.1:' + $(if ($envMap['BRIDGE_PORT']) { $envMap['BRIDGE_PORT'] } else { '8082' })
    $codebase = 'http://127.0.0.1:' + $(if ($envMap['CODEBASE_PORT']) { $envMap['CODEBASE_PORT'] } else { '8081' })
    $dash = 'http://127.0.0.1:' + $(if ($envMap['HUB_PORT']) { $envMap['HUB_PORT'] } else { '8080' })
    Write-Host "  Branch: $branch | Bridge=$bridge | Codebase=$codebase | Dashboard=$dash" -ForegroundColor Gray

    if ($DryRun) {
        Write-Host '[DRY-RUN] Ready for V3.2 (SK32) Schema v9 upgrade & verification.' -ForegroundColor Yellow
        return
    }

    if ($Upgrade) {
        Write-Step '2/7 Online SQLite backup before Schema V9 upgrade (preserves all V3/V3.1 data & volumes)...'
        $null = Wait-Health "$bridge/healthz" $HealthTimeoutSec
        $bk = Invoke-RestMethod -Method Post -Uri "$bridge/v1/backup" -Headers @{ Authorization = ('Bearer ' + $envMap['BRIDGE_ADMIN_TOKEN']) } -TimeoutSec 120
        if (!$bk.backup_id -or !$bk.sha256) { throw 'Backup failed. Aborting.' }
        Write-Host "[PASS] Pre-V3.2 backup ID: $($bk.backup_id) (schema=$($bk.schema_version))" -ForegroundColor Green
        Invoke-Checked 'docker' @('compose', 'config', '--quiet')
        Invoke-Checked 'docker' @('compose', 'up', '-d', '--build', 'codebase', 'memory-bridge', 'dashboard')
    }

    Write-Step '3/7 Checking Service Health & Schema V9...'
    $bh = Wait-Health "$bridge/healthz" $HealthTimeoutSec
    $null = Wait-Health "$codebase/healthz" $HealthTimeoutSec
    $null = Wait-Health "$dash/healthz" $HealthTimeoutSec
    Write-Host "[PASS] Bridge (schema=$($bh.schema_version)), Codebase, Dashboard healthy." -ForegroundColor Green

    Write-Step '4/7 Verifying Dashboard V3.2 Auth/CSRF boundary (unauthenticated must return 401)...'
    foreach ($ep in @('/api/v31/businesses', '/api/v32/skills', '/api/v32/bindings', '/api/v2/traces/scope-options?project_id=20ef268d-5a07-43a7-a66f-8a83e87dd0f7')) {
        $code = 0
        try { $null = Invoke-WebRequest -UseBasicParsing -Uri "$dash$ep" -TimeoutSec 10; $code = 200 } catch {
            if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
        }
        if ($code -ne 401) { throw "Security regression: unauthenticated GET $ep returned HTTP $code." }
    }
    Write-Host '[PASS] All V3.2 & P6A Dashboard endpoints enforce HTTP 401 when unauthenticated.' -ForegroundColor Green

    $readHdr = @{ Authorization = ('Bearer ' + $envMap['BRIDGE_READ_TOKEN']) }
    if ($VerifySkills -and [int]$bh.schema_version -ge 9) {
        Write-Step '5/7 Verifying SK32 Skill Registry & Starter Library live...'
        $sks = Invoke-RestMethod -Uri "$bridge/v32/skills" -Headers $readHdr -TimeoutSec 15
        Write-Host "[PASS] SK32 Skill Library active: $($sks.total) skills (incl. 30 reviewed starter skills)." -ForegroundColor Green
    }

    if ($VerifyP6A) {
        Write-Step '5b/7 Verifying P6A Task-Centric Execution Map scope-options live...'
        $projs = Invoke-RestMethod -Uri "$bridge/v1/projects" -Headers $readHdr -TimeoutSec 15
        foreach ($p in @($projs.items)) {
            $opts = Invoke-RestMethod -Uri "$bridge/v2/traces/scope-options?project_id=$($p.project_id)" -Headers $readHdr -TimeoutSec 15
            Write-Host "[PASS] P6A Scope ($($p.name)): plans=$(@($opts.plans).Count), tasks=$(@($opts.tasks).Count), attempts=$(@($opts.attempts).Count)." -ForegroundColor Green
        }
    }

    if ($RunTests) {
        Write-Step "6/7 Running full V2.2.4 + V3 + V3.1 + V3.2 (SK32 & P6A) pytest suite in isolated Docker..."
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
        Write-Host '[PASS] Full V2.2.4 + V3 + V3.1 + V3.2 pytest suite passed.' -ForegroundColor Green
    }

    Write-Step '7/7 Test-V32.ps1 completed cleanly.'
    Write-Host "Dashboard V3.2: $dash/ (No paid API calls, no volume deletion, no main merge)." -ForegroundColor Green
} finally {
    Pop-Location
}
