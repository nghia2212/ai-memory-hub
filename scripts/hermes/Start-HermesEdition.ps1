<# 
Native Hermes pilot launcher. No legacy dispatcher, no model call, no data migration.
Install official Hermes separately, and configure an isolated pilot profile before execution.
#>
[CmdletBinding()]
param(
    [ValidateSet('Check','CreateBoard','List','Dashboard','Gateway')]
    [string]$Action = 'Check',
    [ValidatePattern('^[a-z][a-z0-9-]{0,62}$')]
    [string]$Profile = 'hub-pilot',
    [ValidatePattern('^[a-z][a-z0-9-]{0,62}$')]
    [string]$Board = 'hub-pilot',
    [ValidateRange(1024,65535)]
    [int]$DashboardPort = 9119
)
$ErrorActionPreference = 'Stop'
if ($Profile -eq 'default' -or $Board -eq 'default') { throw 'Use a dedicated pilot profile and board.' }
$cli = Get-Command hermes -CommandType Application,ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
if (!$cli) { throw 'Hermes is missing. Install the official release v0.23.4, then open a new PowerShell window.' }
function Invoke-Hermes([string[]]$Arguments) {
    & $cli.Source @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Hermes command failed (exit {0}).' -f $LASTEXITCODE) }
}
$version = (Invoke-Hermes -Arguments @('--version') | Out-String).Trim()
if ($version -notmatch '(?<![0-9.])v?0\.23\.4(?![0-9.]|[-+a-zA-Z])') {
    throw 'Unsupported Hermes version. Validate this release and update the pilot pin before proceeding.'
}
# Never switch the sticky default profile or modify an existing user's settings.
$null = Invoke-Hermes -Arguments @('profile','show',$Profile)
switch ($Action) {
    'Check' {
        $null = Invoke-Hermes -Arguments @('-p',$Profile,'kanban','--help')
        $null = Invoke-Hermes -Arguments @('-p',$Profile,'dashboard','--help')
        $null = Invoke-Hermes -Arguments @('-p',$Profile,'gateway','--help')
        Write-Host "Hermes native CLI: verified version and command availability only; provider, workers and recovery not verified."
        foreach ($name in @('claude','codex','gemini','agy')) {
            if (Get-Command $name -ErrorAction SilentlyContinue) { Write-Host "$name : executable found (auth and quota unknown)" }
            else { Write-Host "$name : executable not found" }
        }
    }
    'CreateBoard' {
        Invoke-Hermes -Arguments @('-p',$Profile,'kanban','boards','create',$Board,'--name','AI Memory Hub pilot')
    }
    'List' {
        Invoke-Hermes -Arguments @('-p',$Profile,'kanban','--board',$Board,'list','--json')
    }
    'Dashboard' {
        Invoke-Hermes -Arguments @('-p',$Profile,'dashboard','--host','127.0.0.1','--port',"$DashboardPort",'--no-open')
    }
    'Gateway' {
        # Explicit operator action only. Gateway may dispatch approved test cards and call configured models.
        Invoke-Hermes -Arguments @('-p',$Profile,'gateway','run')
    }
}
