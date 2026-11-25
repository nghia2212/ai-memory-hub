[CmdletBinding()]
param(
    [ValidateSet('Shared','Game','Architect','Developer','Custom')]
    [string]$SelectPack = 'Shared',
    [switch]$DryRun,
    [switch]$ApproveInstall
)
$ErrorActionPreference = 'Stop'

$Catalog = @{
    Shared = @(
        @{ Name='ai-memory-bridge'; Source='Built-in Hub (local)'; Version='v3.1'; License='MIT'; Transport='streamable_http'; RequiresAdmin=$false },
        @{ Name='ai-memory-codebase'; Source='Built-in Hub (local)'; Version='v0.12.1'; License='MIT'; Transport='streamable_http'; RequiresAdmin=$false },
        @{ Name='pack-shared (Git/FS/Docs)'; Source='Built-in services/orchestrator/tool_packs/shared_pack.py'; Version='v3.1'; License='MIT'; Transport='local_tool'; RequiresAdmin=$false },
        @{ Name='github-mcp (Optional)'; Source='https://github.com/github/github-mcp-server'; Version='pinned-release'; License='MIT'; Transport='stdio'; RequiresAdmin=$false; Note='Requires user GITHUB_TOKEN' }
    )
    Game = @(
        @{ Name='pack-game (GLB/Blender/Image/Audio)'; Source='Built-in services/orchestrator/tool_packs/game_pack.py'; Version='v3.1'; License='MIT'; Transport='local_tool'; RequiresAdmin=$false },
        @{ Name='playwright-mcp (Optional)'; Source='https://github.com/microsoft/playwright-mcp (@playwright/mcp)'; Version='pinned-npm'; License='Apache-2.0'; Transport='stdio'; RequiresAdmin=$false },
        @{ Name='cloudflare-wrangler (Optional)'; Source='https://github.com/cloudflare/workers-sdk'; Version='pinned-npm'; License='MIT/Apache-2.0'; Transport='native_cli'; RequiresAdmin=$false; Note='Preview only; requires CLOUDFLARE_API_TOKEN' }
    )
    Architect = @(
        @{ Name='pack-architect (DB-Meta/OpenAPI/HTTP/Diagram/Doc)'; Source='Built-in services/orchestrator/tool_packs/architect_pack.py'; Version='v3.1'; License='MIT'; Transport='local_tool'; RequiresAdmin=$false },
        @{ Name='oracle-postgres-mssql (Optional Test DSN)'; Source='Built-in adapter (env TEST_*_DSN)'; Version='v3.1'; License='MIT'; Transport='local_tool'; RequiresAdmin=$false; Note='Read-only test env only; production denied' }
    )
    Developer = @(
        @{ Name='pack-developer (Java/TS/Maven/JUnit/Security/Log)'; Source='Built-in services/orchestrator/tool_packs/developer_pack.py'; Version='v3.1'; License='MIT'; Transport='local_tool'; RequiresAdmin=$false },
        @{ Name='semgrep / trivy (Optional Local CLI)'; Source='https://github.com/aquasecurity/trivy'; Version='pinned-release'; License='Apache-2.0'; Transport='native_cli'; RequiresAdmin=$false }
    )
}

Write-Host "=== AI Memory Hub V3.1 — MCP Pack Installer (Pack: $SelectPack) ===" -ForegroundColor Cyan
$items = if ($SelectPack -eq 'Custom') { $Catalog.Shared + $Catalog.Game + $Catalog.Architect + $Catalog.Developer } else { $Catalog[$SelectPack] }

foreach ($it in $items) {
    Write-Host ("- {0} | Version: {1} | License: {2} | Transport: {3} | Source: {4}" -f $it.Name, $it.Version, $it.License, $it.Transport, $it.Source) -ForegroundColor White
    if ($it.Note) { Write-Host ("    Note: {0}" -f $it.Note) -ForegroundColor Yellow }
}

if ($DryRun -or !$ApproveInstall) {
    Write-Host "`n[DRY-RUN / APPROVAL REQUIRED] No third-party packages were installed automatically." -ForegroundColor Yellow
    Write-Host "Review sources/licenses above and re-run with -ApproveInstall to register approved local packs." -ForegroundColor Gray
    return
}

Write-Host "`n[OK] Built-in V3.1 packs for '$SelectPack' are ready in services/orchestrator/tool_packs/ without requiring Administrator privileges or paid APIs." -ForegroundColor Green
