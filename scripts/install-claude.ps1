<#
.SYNOPSIS
Idempotent, fail-safe Windows installer for AI Memory Hub Claude Code MCP & Hooks.

.DESCRIPTION
Provisions local secrets into .env (preserving existing values), backs up existing
Claude Code configs, merges MCP servers (ai-memory-codebase, ai-memory-bridge) and
hooks (SessionStart, Stop, SessionEnd) without overwriting user entries, supports
-DryRun, conflict detection, -Force, and safe -Uninstall of only AI Memory Hub entries.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Uninstall,
    [switch]$Force,
    [string]$SettingsPath = (Join-Path $env:USERPROFILE '.claude\settings.json'),
    [string]$McpConfigPath = (Join-Path $env:USERPROFILE '.claude.json'),
    [string]$EnvFilePath = '',
    [string]$CodebaseUrl = '',
    [string]$BridgeUrl = '',
    [string]$PythonExe = 'python'
)

$ErrorActionPreference = 'Stop'

$HubRoot = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($EnvFilePath)) {
    $EnvFilePath = Join-Path $HubRoot '.env'
}

$Timestamp = Get-Date -Format 'yyyyMMddHHmmss'
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

function Read-EnvMap {
    param([string]$Path)
    $map = [ordered]@{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
            $trimmed = $line.Trim()
            if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('#')) {
                continue
            }
            $idx = $trimmed.IndexOf('=')
            if ($idx -gt 0) {
                $k = $trimmed.Substring(0, $idx).Trim()
                $v = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
                $map[$k] = $v
            }
        }
    }
    return $map
}

function Backup-FileIfExists {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        $bak = "$Path.bak.$Timestamp"
        if ($DryRun) {
            Write-Host "[DRY-RUN] Would backup '$Path' -> '$bak'"
        } else {
            Copy-Item -LiteralPath $Path -Destination $bak -Force
            Write-Host "Backed up '$Path' -> '$bak'"
        }
    }
}

function Write-TextAtomic {
    param([string]$Path, [string]$Content)
    if ($DryRun) {
        Write-Host "[DRY-RUN] Would write '$Path'"
        return
    }
    $parent = Split-Path $Path -Parent
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $tmp = "$Path.tmp.$PID.$Timestamp"
    [System.IO.File]::WriteAllText($tmp, $Content, $Utf8NoBom)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Read-JsonObject {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return (New-Object PSObject)
    }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return (New-Object PSObject)
    }
    $obj = $raw | ConvertFrom-Json
    if ($null -eq $obj) {
        return (New-Object PSObject)
    }
    return $obj
}

function Ensure-Prop {
    param([PSObject]$Obj, [string]$Name, $DefaultValue)
    $prop = $Obj.PSObject.Properties[$Name]
    if ($null -eq $prop -or $null -eq $prop.Value) {
        $Obj | Add-Member -MemberType NoteProperty -Name $Name -Value $DefaultValue -Force
    }
    return $Obj.PSObject.Properties[$Name].Value
}

function Test-IsHubHookEntry {
    param($Entry)
    if ($null -eq $Entry -or $null -eq $Entry.hooks) {
        return $false
    }
    foreach ($h in @($Entry.hooks)) {
        if ($null -ne $h.command) {
            $cmd = [string]$h.command
            if ($cmd -match 'integrations[\\/]claude[\\/]hooks[\\/](session_start|session_event)\.py') {
                return $true
            }
        }
    }
    return $false
}

function Build-HookEntry {
    param([string]$ScriptRelPath, [string]$ExtraArgs = '')
    $scriptAbs = Join-Path $HubRoot $ScriptRelPath
    $cmd = "`"$PythonExe`" `"$scriptAbs`""
    if (-not [string]::IsNullOrWhiteSpace($ExtraArgs)) {
        $cmd = "$cmd $ExtraArgs"
    }
    return [PSCustomObject]@{
        matcher = ''
        hooks   = @(
            [PSCustomObject]@{
                type    = 'command'
                command = $cmd
                timeout = 5
            }
        )
    }
}

# --- UNINSTALL FLOW ---
if ($Uninstall) {
    Write-Host "Uninstalling AI Memory Hub entries (preserving user config)..."
    if (Test-Path -LiteralPath $McpConfigPath) {
        Backup-FileIfExists -Path $McpConfigPath
        $mcpObj = Read-JsonObject -Path $McpConfigPath
        $servers = $mcpObj.PSObject.Properties['mcpServers']
        if ($null -ne $servers -and $null -ne $servers.Value) {
            foreach ($name in @('ai-memory-codebase', 'ai-memory-bridge')) {
                if ($null -ne $servers.Value.PSObject.Properties[$name]) {
                    $servers.Value.PSObject.Properties.Remove($name)
                    Write-Host "Removed MCP server '$name' from '$McpConfigPath'"
                }
            }
        }
        Write-TextAtomic -Path $McpConfigPath -Content ($mcpObj | ConvertTo-Json -Depth 12)
    }

    if (Test-Path -LiteralPath $SettingsPath) {
        Backup-FileIfExists -Path $SettingsPath
        $settingsObj = Read-JsonObject -Path $SettingsPath
        $hooksProp = $settingsObj.PSObject.Properties['hooks']
        if ($null -ne $hooksProp -and $null -ne $hooksProp.Value) {
            foreach ($eventName in @('SessionStart', 'Stop', 'SessionEnd')) {
                $evProp = $hooksProp.Value.PSObject.Properties[$eventName]
                if ($null -ne $evProp -and $null -ne $evProp.Value) {
                    $filtered = @($evProp.Value | Where-Object { -not (Test-IsHubHookEntry $_) })
                    $hooksProp.Value | Add-Member -MemberType NoteProperty -Name $eventName -Value $filtered -Force
                }
            }
        }
        Write-TextAtomic -Path $SettingsPath -Content ($settingsObj | ConvertTo-Json -Depth 12)
    }

    Write-Host "Uninstall complete."
    exit 0
}

# --- INSTALL / MERGE FLOW ---
$envMap = Read-EnvMap -Path $EnvFilePath
$codebasePort = if ($envMap.Contains('CODEBASE_PORT') -and $envMap['CODEBASE_PORT']) { $envMap['CODEBASE_PORT'] } else { '8081' }
$bridgePort = if ($envMap.Contains('BRIDGE_PORT') -and $envMap['BRIDGE_PORT']) { $envMap['BRIDGE_PORT'] } else { '8082' }
if ([string]::IsNullOrWhiteSpace($CodebaseUrl)) { $CodebaseUrl = "http://127.0.0.1:$codebasePort/mcp" }
if ([string]::IsNullOrWhiteSpace($BridgeUrl)) { $BridgeUrl = "http://127.0.0.1:$bridgePort/mcp" }

$requiredSecrets = @(
    'BRIDGE_READ_TOKEN',
    'BRIDGE_WRITE_TOKEN',
    'BRIDGE_ADMIN_TOKEN',
    'BRIDGE_WORKER_TOKEN',
    'CODEBASE_TOKEN'
)

$envUpdated = $false
if (-not $envMap.Contains('HUB_PORT')) { $envMap['HUB_PORT'] = '8080'; $envUpdated = $true }
if (-not $envMap.Contains('CODEBASE_PORT')) { $envMap['CODEBASE_PORT'] = $codebasePort; $envUpdated = $true }
if (-not $envMap.Contains('BRIDGE_PORT')) { $envMap['BRIDGE_PORT'] = $bridgePort; $envUpdated = $true }
if (-not $envMap.Contains('HERMES_ENABLED')) { $envMap['HERMES_ENABLED'] = 'false'; $envUpdated = $true }
if (-not $envMap.Contains('HERMES_PROVIDER')) { $envMap['HERMES_PROVIDER'] = 'deepseek'; $envUpdated = $true }

foreach ($sec in $requiredSecrets) {
    if (-not $envMap.Contains($sec) -or [string]::IsNullOrWhiteSpace($envMap[$sec])) {
        $envMap[$sec] = New-RandomSecret
        $envUpdated = $true
    }
}

# Load existing Claude configs and detect conflicts BEFORE writing anything
$mcpObj = Read-JsonObject -Path $McpConfigPath
$mcpServers = Ensure-Prop -Obj $mcpObj -Name 'mcpServers' -DefaultValue (New-Object PSObject)

$desiredServers = [ordered]@{
    'ai-memory-codebase' = [PSCustomObject]@{
        type    = 'http'
        url     = $CodebaseUrl
        headers = [PSCustomObject]@{
            Authorization = "Bearer $($envMap['CODEBASE_TOKEN'])"
        }
    }
    'ai-memory-bridge' = [PSCustomObject]@{
        type    = 'http'
        url     = $BridgeUrl
        headers = [PSCustomObject]@{
            Authorization = "Bearer $($envMap['BRIDGE_READ_TOKEN'])"
        }
    }
}

foreach ($srvName in $desiredServers.Keys) {
    $existingProp = $mcpServers.PSObject.Properties[$srvName]
    if ($null -ne $existingProp -and $null -ne $existingProp.Value) {
        $existingUrl = [string]($existingProp.Value.url)
        $existingType = [string]($existingProp.Value.type)
        $targetUrl = [string]($desiredServers[$srvName].url)
        if (($existingUrl -ne $targetUrl -or $existingType -ne 'http') -and -not $Force) {
            throw "Conflict detected for MCP server '$srvName' in '$McpConfigPath' (existing: type=$existingType url=$existingUrl, desired: type=http url=$targetUrl). Re-run with -Force to override."
        }
    }
    $mcpServers | Add-Member -MemberType NoteProperty -Name $srvName -Value $desiredServers[$srvName] -Force
}

$settingsObj = Read-JsonObject -Path $SettingsPath
$hooksObj = Ensure-Prop -Obj $settingsObj -Name 'hooks' -DefaultValue (New-Object PSObject)

$desiredHooks = [ordered]@{
    'SessionStart' = Build-HookEntry -ScriptRelPath 'integrations\claude\hooks\session_start.py'
    'Stop'         = Build-HookEntry -ScriptRelPath 'integrations\claude\hooks\session_event.py' -ExtraArgs '--kind stop'
    'SessionEnd'   = Build-HookEntry -ScriptRelPath 'integrations\claude\hooks\session_event.py' -ExtraArgs '--kind session_end'
}

foreach ($evName in $desiredHooks.Keys) {
    $existingArr = Ensure-Prop -Obj $hooksObj -Name $evName -DefaultValue @()
    $kept = @($existingArr | Where-Object { -not (Test-IsHubHookEntry $_) })
    $merged = @($kept) + @($desiredHooks[$evName])
    $hooksObj | Add-Member -MemberType NoteProperty -Name $evName -Value $merged -Force
}

# Back up and write atomically
if ($envUpdated -or -not (Test-Path -LiteralPath $EnvFilePath)) {
    Backup-FileIfExists -Path $EnvFilePath
    $envLines = New-Object System.Collections.Generic.List[string]
    $envLines.Add('# Generated/updated by install-claude.ps1 (never commit)')
    foreach ($k in $envMap.Keys) {
        $envLines.Add("$k=$($envMap[$k])")
    }
    Write-TextAtomic -Path $EnvFilePath -Content (($envLines -join "`n") + "`n")
}

Backup-FileIfExists -Path $McpConfigPath
Write-TextAtomic -Path $McpConfigPath -Content ($mcpObj | ConvertTo-Json -Depth 12)

Backup-FileIfExists -Path $SettingsPath
Write-TextAtomic -Path $SettingsPath -Content ($settingsObj | ConvertTo-Json -Depth 12)

Write-Host "AI Memory Hub Claude Code integration completed (DryRun=$DryRun)."
Write-Host "  MCP config : $McpConfigPath (ai-memory-codebase -> $CodebaseUrl, ai-memory-bridge -> $BridgeUrl)"
Write-Host "  Hooks      : $SettingsPath (SessionStart, Stop, SessionEnd)"
Write-Host "  Secrets    : $EnvFilePath (tokens provisioned without printing)"
