<# Start only existing memory/index services, not the legacy orchestrator UI or runner. #>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
Push-Location -LiteralPath $root
try {
    if (!(Test-Path -LiteralPath (Join-Path $root '.env') -PathType Leaf)) {
        throw 'Missing .env; restore existing local configuration or run scripts/init-env.ps1.'
    }
    $running = @(& docker compose ps --status running --services)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect legacy Docker services.' }
    if ($running -contains 'dashboard' -or $running -contains 'hermes') {
        throw 'Legacy Dashboard/worker is running. Drain existing tasks and stop the legacy Hub explicitly before this mode.'
    }
    # Existing named volumes are reused; never delete, restore or migrate live task data here.
    & docker compose up -d --no-deps codebase memory-bridge
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start memory services.' }
    Write-Host 'Memory services requested. Check their health before registering MCP.'
    Write-Host 'No legacy Host Runner was started. Ensure existing runners are stopped separately.'
    & docker compose ps codebase memory-bridge
    if ($LASTEXITCODE -ne 0) { throw 'Failed to inspect memory services.' }
} finally { Pop-Location }
