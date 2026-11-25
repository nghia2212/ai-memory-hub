[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\workspace1\ai-memory-hub',
    [switch]$Upgrade,
    [switch]$RunTests
)
$ErrorActionPreference = 'Stop'
$branch = 'lead/v21-development-workbench'
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
function Wait-Health([string]$Url) {
    for ($i=0; $i -lt 90; $i++) {
        try {
            $h = Invoke-RestMethod -Uri $Url -TimeoutSec 4
            if ($h.ready -eq $true) { return $h }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw "Health check failed: $Url"
}
function Get-Port($Map, [string]$Key, [int]$Default) {
    if ($Map[$Key]) { return [int]$Map[$Key] }
    return $Default
}
if (!(Test-Path -LiteralPath $RepoPath -PathType Container)) { throw 'Repository folder not found. Set -RepoPath.' }
Push-Location -LiteralPath $RepoPath
try {
    Invoke-Checked 'git' @('rev-parse','--is-inside-work-tree')
    Invoke-Checked 'docker' @('info','--format','{{.ServerVersion}}')
    if (!(Test-Path -LiteralPath '.env')) { throw 'Existing .env is required. Complete V2.1 setup first.' }
    $envMap = Read-Env
    $bridge = 'http://127.0.0.1:' + (Get-Port $envMap 'BRIDGE_PORT' 8082)
    $dash = 'http://127.0.0.1:' + (Get-Port $envMap 'HUB_PORT' 8080)
    if ($Upgrade) {
        $current = (& git branch --show-current).Trim()
        if ($LASTEXITCODE -ne 0 -or $current -ne $branch) { throw "Checkout $branch first. No automatic branch switch." }
        $dirty = @(& git status --porcelain)
        if ($LASTEXITCODE -ne 0 -or $dirty.Count -gt 0) { throw 'Working tree is not clean. Commit or stash changes yourself.' }
        if (!$envMap['BRIDGE_ADMIN_TOKEN']) { throw 'Missing BRIDGE_ADMIN_TOKEN.' }
        $null = Wait-Health "$bridge/healthz"
        $backup = Invoke-RestMethod -Method Post -Uri "$bridge/v1/backup" -Headers @{Authorization=('Bearer '+$envMap['BRIDGE_ADMIN_TOKEN'])} -TimeoutSec 120
        if (!$backup.backup_id -or !$backup.sha256) { throw 'Backup not confirmed. Upgrade aborted.' }
        Write-Host "[PASS] Verified backup ID: $($backup.backup_id)"
        Invoke-Checked 'git' @('fetch','origin',$branch)
        Invoke-Checked 'git' @('merge','--ff-only','FETCH_HEAD')
        $init = Join-Path (Get-Location).Path 'scripts\init-env.ps1'
        Invoke-Checked 'powershell.exe' @('-NoProfile','-ExecutionPolicy','Bypass','-File',$init)
        Invoke-Checked 'docker' @('compose','config','--quiet')
        Invoke-Checked 'docker' @('compose','up','-d','--build','codebase','memory-bridge','dashboard')
        $envMap = Read-Env
    }
    $health = Wait-Health "$bridge/healthz"
    if ([int]$health.schema_version -lt 4) { throw 'V2.2 schema migration 4 is missing.' }
    Write-Host "[PASS] Bridge schema $($health.schema_version)"
    $null = Wait-Health "$dash/healthz"
    $html = (Invoke-WebRequest -UseBasicParsing -Uri "$dash/" -TimeoutSec 10).Content
    foreach ($label in @('trace-nav','Graph View','Swimlane View','Timeline View','Table View','trace-metrics')) {
        if (!$html.Contains($label)) { throw "Missing dashboard element: $label" }
    }
    $js = (Invoke-WebRequest -UseBasicParsing -Uri "$dash/execution-map.js" -TimeoutSec 10).Content
    if (!$js.Contains('renderMetrics')) { throw 'Execution Map script missing.' }
    Write-Host '[PASS] Dashboard four views and script present (not browser rendering test).'
    if (!$envMap['BRIDGE_READ_TOKEN']) { throw 'Missing BRIDGE_READ_TOKEN.' }
    $headers = @{Authorization=('Bearer '+$envMap['BRIDGE_READ_TOKEN'])}
    $projects = Invoke-RestMethod -Uri "$bridge/v1/projects" -Headers $headers -TimeoutSec 15
    foreach ($p in @($projects.items)) {
        if (!$p.project_id) { continue }
        $r = Invoke-RestMethod -Uri "$bridge/v2/traces?project_id=$($p.project_id)&limit=1" -Headers $headers -TimeoutSec 15
        if ($null -eq $r.metrics -or $null -eq $r.total) { throw 'Invalid trace response.' }
        Write-Host "[PASS] Trace API: $($p.project_id) - $($r.total) observed steps"
    }
    if (@($projects.items).Count -eq 0) { Write-Host '[SKIP] No registered projects; scoped trace query not exercised.' }
    if ($RunTests) {
        $root = (Get-Location).Path
        # Run tests in disposable image containers, with source mounted read-only.
        $mount = "type=bind,source=$root,target=/repo,readonly"
        foreach ($suite in @('bridge','dashboard')) {
            $test = if ($suite -eq 'bridge') { 'tests/bridge/test_v22_traces.py tests/bridge/test_v2_bridge.py tests/bridge/test_v21_lease_recovery.py tests/bridge/test_bridge_core.py tests/bridge/test_mcp_handshake.py' } else { 'tests/dashboard/test_v2_dashboard.py tests/dashboard/test_dashboard.py' }
            $cmd = "python -m venv /tmp/v22test && /tmp/v22test/bin/pip install -q -r /repo/services/memory-bridge/requirements.txt -r /repo/services/dashboard/requirements.txt pytest && cd /repo && PYTHONPATH=/repo /tmp/v22test/bin/python -m pytest -p no:cacheprovider -q $test"
            Invoke-Checked 'docker' @('run','--rm','--mount',$mount,'-e','PYTHONDONTWRITEBYTECODE=1','python:3.12-slim','sh','-c',$cmd)
        }
        Write-Host '[PASS] Focused pytest suites.'
    }
    Write-Host "Dashboard: $dash/"
    Write-Host 'Manual: login, open Execution Map, switch 4 views, test filters and evidence.'
    Write-Host 'No AI model calls made by this script. No volumes removed.'
} finally { Pop-Location }
