<#
.SYNOPSIS
Idempotent, fail-safe Windows installer for AI Memory Hub Gemini CLI MCP & Hooks.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Uninstall,
    [switch]$Force,
    [string]$SettingsPath = (Join-Path $env:USERPROFILE '.gemini\settings.json'),
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
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
}

function Read-EnvMap {
    param([string]$Path)
    $map = [ordered]@{}
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
            $trimmed = $line.Trim()
            if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('#')) { continue }
            $idx = $trimmed.IndexOf('=')
            if ($idx -gt 0) {
                $map[$trimmed.Substring(0, $idx).Trim()] = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
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
    if (-not (Test-Path -LiteralPath $Path)) { return (New-Object PSObject) }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) { return (New-Object PSObject) }
    $obj = $raw | ConvertFrom-Json
    if ($null -eq $obj) { return (New-Object PSObject) }
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

function Test-IsHubGeminiHookEntry {
    param($Entry)
    if ($null -eq $Entry -or $null -eq $Entry.hooks) { return $false }
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

function Build-GeminiHookEntry {
    param([string]$Name, [string]$Matcher, [string]$ScriptRelPath, [string]$ExtraArgs)
    $scriptAbs = Join-Path $HubRoot $ScriptRelPath
    $cmd = "`"$PythonExe`" `"$scriptAbs`" $ExtraArgs"
    return [PSCustomObject]@{
        matcher = $Matcher
        hooks   = @(
            [PSCustomObject]@{
                name    = $Name
                type    = 'command'
                command = $cmd
                timeout = 5000
            }
        )
    }
}

$geminiCmd = Get-Command gemini -ErrorAction SilentlyContinue
if ($geminiCmd) {
    $ver = (& gemini --version 2>$null) -join ' '
    Write-Host "Detected Gemini CLI: $ver"
} else {
    Write-Host "Gemini CLI not found on PATH (configuring '$SettingsPath' anyway)."
}

if ($Uninstall) {
    Write-Host "Uninstalling AI Memory Hub Gemini CLI entries..."
    if (Test-Path -LiteralPath $SettingsPath) {
        Backup-FileIfExists -Path $SettingsPath
        $settingsObj = Read-JsonObject -Path $SettingsPath
        $serversProp = $settingsObj.PSObject.Properties['mcpServers']
        if ($null -ne $serversProp -and $null -ne $serversProp.Value) {
            foreach ($name in @('ai-memory-codebase', 'ai-memory-bridge')) {
                if ($null -ne $serversProp.Value.PSObject.Properties[$name]) {
                    $serversProp.Value.PSObject.Properties.Remove($name)
                }
            }
        }
        $hooksProp = $settingsObj.PSObject.Properties['hooks']
        if ($null -ne $hooksProp -and $null -ne $hooksProp.Value) {
            foreach ($evName in @('SessionStart', 'SessionEnd')) {
                $evProp = $hooksProp.Value.PSObject.Properties[$evName]
                if ($null -ne $evProp -and $null -ne $evProp.Value) {
                    $filtered = @($evProp.Value | Where-Object { -not (Test-IsHubGeminiHookEntry $_) })
                    $hooksProp.Value | Add-Member -MemberType NoteProperty -Name $evName -Value $filtered -Force
                }
            }
        }
        Write-TextAtomic -Path $SettingsPath -Content ($settingsObj | ConvertTo-Json -Depth 12)
    }
    Write-Host "Gemini CLI uninstall complete."
    exit 0
}

$envMap = Read-EnvMap -Path $EnvFilePath
$codebasePort = if ($envMap.Contains('CODEBASE_PORT') -and $envMap['CODEBASE_PORT']) { $envMap['CODEBASE_PORT'] } else { '8081' }
$bridgePort = if ($envMap.Contains('BRIDGE_PORT') -and $envMap['BRIDGE_PORT']) { $envMap['BRIDGE_PORT'] } else { '8082' }
if ([string]::IsNullOrWhiteSpace($CodebaseUrl)) { $CodebaseUrl = "http://127.0.0.1:$codebasePort/mcp" }
if ([string]::IsNullOrWhiteSpace($BridgeUrl)) { $BridgeUrl = "http://127.0.0.1:$bridgePort/mcp" }

$requiredSecrets = @('BRIDGE_READ_TOKEN', 'BRIDGE_WRITE_TOKEN', 'BRIDGE_ADMIN_TOKEN', 'BRIDGE_WORKER_TOKEN', 'CODEBASE_TOKEN')
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

$settingsObj = Read-JsonObject -Path $SettingsPath
$mcpServers = Ensure-Prop -Obj $settingsObj -Name 'mcpServers' -DefaultValue (New-Object PSObject)

$desiredServers = [ordered]@{
    'ai-memory-codebase' = [PSCustomObject]@{
        httpUrl = $CodebaseUrl
        headers = [PSCustomObject]@{ Authorization = "Bearer $($envMap['CODEBASE_TOKEN'])" }
    }
    'ai-memory-bridge' = [PSCustomObject]@{
        httpUrl = $BridgeUrl
        headers = [PSCustomObject]@{ Authorization = "Bearer $($envMap['BRIDGE_READ_TOKEN'])" }
    }
}

foreach ($srvName in $desiredServers.Keys) {
    $existingProp = $mcpServers.PSObject.Properties[$srvName]
    if ($null -ne $existingProp -and $null -ne $existingProp.Value) {
        $existingUrl = [string]($existingProp.Value.httpUrl)
        $targetUrl = [string]($desiredServers[$srvName].httpUrl)
        if ($existingUrl -ne $targetUrl -and -not $Force) {
            throw "Conflict detected for Gemini MCP server '$srvName' in '$SettingsPath' (existing: $existingUrl, desired: $targetUrl). Re-run with -Force to override."
        }
    }
    $mcpServers | Add-Member -MemberType NoteProperty -Name $srvName -Value $desiredServers[$srvName] -Force
}

$hooksObj = Ensure-Prop -Obj $settingsObj -Name 'hooks' -DefaultValue (New-Object PSObject)
$desiredHooks = [ordered]@{
    'SessionStart' = Build-GeminiHookEntry -Name 'ai-memory-hub-start' -Matcher 'startup' -ScriptRelPath 'integrations\claude\hooks\session_start.py' -ExtraArgs '--source-agent gemini --format gemini'
    'SessionEnd'   = Build-GeminiHookEntry -Name 'ai-memory-hub-end' -Matcher '*' -ScriptRelPath 'integrations\claude\hooks\session_event.py' -ExtraArgs '--kind session_end --source-agent gemini --format gemini'
}

foreach ($evName in $desiredHooks.Keys) {
    $existingArr = Ensure-Prop -Obj $hooksObj -Name $evName -DefaultValue @()
    $kept = @($existingArr | Where-Object { -not (Test-IsHubGeminiHookEntry $_) })
    $merged = @($kept) + @($desiredHooks[$evName])
    $hooksObj | Add-Member -MemberType NoteProperty -Name $evName -Value $merged -Force
}

if ($envUpdated -or -not (Test-Path -LiteralPath $EnvFilePath)) {
    Backup-FileIfExists -Path $EnvFilePath
    $envLines = New-Object System.Collections.Generic.List[string]
    $envLines.Add('# Generated/updated by installer (never commit)')
    foreach ($k in $envMap.Keys) { $envLines.Add("$k=$($envMap[$k])") }
    Write-TextAtomic -Path $EnvFilePath -Content (($envLines -join "`n") + "`n")
}

Backup-FileIfExists -Path $SettingsPath
Write-TextAtomic -Path $SettingsPath -Content ($settingsObj | ConvertTo-Json -Depth 12)

Write-Host "AI Memory Hub Gemini CLI integration completed (DryRun=$DryRun)."
Write-Host "  Settings : $SettingsPath (MCP httpUrl + SessionStart/SessionEnd hooks)"
Write-Host "  Secrets  : $EnvFilePath (tokens provisioned without printing)"
