[CmdletBinding()]
param([string]$HubRoot = (Split-Path $PSScriptRoot -Parent), [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $HubRoot).Path
$top = (& git -C $root rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0) { throw 'Not a Git worktree' }
if ([IO.Path]::GetFullPath($top) -ne $root) { throw 'HubRoot must be the worktree root' }
$head = (& git -C $root rev-parse HEAD).Trim()
$branch = (& git -C $root branch --show-current).Trim()
if ($DryRun) { Write-Host "Would register Hub worktree at $head; no changes"; return }
$cfg = @{}
Get-Content -LiteralPath (Join-Path $root '.env') | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') { $cfg[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'") }
}
$port = if ($cfg.BRIDGE_PORT) { $cfg.BRIDGE_PORT } else { '8082' }
$url = "http://127.0.0.1:$port"
if (!$cfg.BRIDGE_ADMIN_TOKEN) { throw 'Missing admin token' }
$headers = @{Authorization = "Bearer $($cfg.BRIDGE_ADMIN_TOKEN)"}
function Call-Bridge($method,$path,$body=$null) {
    $p = @{Method=$method; Uri="$url$path"; Headers=$headers; TimeoutSec=30}
    if ($null -ne $body) { $p.ContentType='application/json'; $p.Body=($body | ConvertTo-Json -Depth 8 -Compress) }
    Invoke-RestMethod @p
}
$null = Call-Bridge POST '/v1/backup'
$projects = (Call-Bridge GET '/v1/projects').items
foreach ($p in $projects) {
    $existing = (Call-Bridge GET "/v1/projects/$($p.project_id)/worktrees").items | Where-Object { $_.canonical_root -eq $root }
    if ($existing) {
        if ($p.canonical_identity -ne 'github.com/nghia2212/ai-memory-hub') { throw 'Root registered to another identity; review manually' }
        $project = $p; $wt = $existing; break
    }
}
if (!$project) {
    $project = $projects | Where-Object { $_.canonical_identity -eq 'github.com/nghia2212/ai-memory-hub' }
    if (!$project) { $project = Call-Bridge POST '/v1/projects' @{name='AI Memory Hub';canonical_identity='github.com/nghia2212/ai-memory-hub';hermes_approved=$false} }
    $wt = Call-Bridge POST "/v1/projects/$($project.project_id)/worktrees" @{canonical_root=$root;branch=$branch;head_commit=$head;is_detached=(!$branch);dirty_fingerprint=''}
}
# Only evidence pointers, not source contents or commit messages.
foreach ($file in @('AGENTS.md','README.md','docs/ARCHITECTURE.md','docs/ROADMAP.md','docs/v21/IMPLEMENTATION-PLAN.md')) {
    & git -C $root cat-file -e "${head}:$file" 2>$null
    if ($LASTEXITCODE -ne 0) { continue }
    $blob = (& git -C $root rev-parse "${head}:$file").Trim()
    $null = Call-Bridge POST '/v1/memories' @{project_id=$project.project_id;worktree_id=$wt.worktree_id;type='note';title="Hub evidence: $file";body="Tracked document at commit $head, blob $blob. Review source before use.";source_agent='self-onboard';source_commit=$head;evidence_ref=$file;idempotency_key="hub-doc-$blob"}
}
Write-Host "Registered project $($project.project_id) worktree $($wt.worktree_id). Read-only container mount and indexing require separate verification."
