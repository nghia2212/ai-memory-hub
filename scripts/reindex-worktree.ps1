<#
.SYNOPSIS
Trigger incremental Codebase Memory indexing for a registered Git worktree.

.DESCRIPTION
Resolves the current Git worktree root, matches (project_id, worktree_id) on
Memory Bridge, and calls POST /v1/index (incremental/no_op if unchanged).
Suitable for git post-merge / post-checkout hooks or manual refresh.
#>
[CmdletBinding()]
param(
    [string]$WorktreePath = (Get-Location).Path,
    [ValidateSet('fast','moderate','full')]
    [string]$Mode = 'fast',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$HubRoot = Split-Path $PSScriptRoot -Parent
$EnvPath = Join-Path $HubRoot '.env'
$envMap = @{}
if (Test-Path -LiteralPath $EnvPath) {
    foreach ($line in (Get-Content -LiteralPath $EnvPath -Encoding UTF8)) {
        $t = $line.Trim()
        if ($t -and !$t.StartsWith('#') -and $t.Contains('=')) {
            $k, $v = $t.Split('=', 2)
            $envMap[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
        }
    }
}

$bridgePort = if ($envMap['BRIDGE_PORT']) { $envMap['BRIDGE_PORT'] } else { '8082' }
$bridgeUrl = "http://127.0.0.1:$bridgePort"
$readH = @{ Authorization = "Bearer $($envMap['BRIDGE_READ_TOKEN'])" }
$adminH = @{ Authorization = "Bearer $($envMap['BRIDGE_ADMIN_TOKEN'])"; "Content-Type" = "application/json" }

$top = (git -C $WorktreePath rev-parse --show-toplevel).Trim()
$normTop = [System.IO.Path]::GetFullPath($top).ToLowerInvariant()

$projects = (Invoke-RestMethod -Uri "$bridgeUrl/v1/projects" -Headers $readH -TimeoutSec 5).items
$matchedPid = $null; $matchedWid = $null
foreach ($p in $projects) {
    $wts = (Invoke-RestMethod -Uri "$bridgeUrl/v1/projects/$($p.project_id)/worktrees" -Headers $readH -TimeoutSec 5).items
    foreach ($w in $wts) {
        if ($w.canonical_root -and ([System.IO.Path]::GetFullPath($w.canonical_root).ToLowerInvariant() -eq $normTop)) {
            $matchedPid = $p.project_id
            $matchedWid = $w.worktree_id
            break
        }
    }
    if ($matchedWid) { break }
}

if (-not $matchedWid) {
    Write-Error "Worktree '$top' is not registered in AI Memory Hub."
    exit 1
}

$body = @{ project_id = $matchedPid; worktree_id = $matchedWid; mode = $Mode; force = [bool]$Force } | ConvertTo-Json
$res = Invoke-RestMethod -Method Post -Uri "$bridgeUrl/v1/index" -Headers $adminH -Body $body -TimeoutSec 120
$res | ConvertTo-Json -Depth 4
