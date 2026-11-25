<#
.SYNOPSIS
Idempotent, fail-safe Windows installer for AI Memory Hub Codex CLI MCP & Hooks.
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Uninstall,
    [switch]$Force,
    [string]$ConfigTomlPath = (Join-Path $env:USERPROFILE '.codex\config.toml'),
    [string]$HooksPath = (Join-Path $env:USERPROFILE '.codex\hooks.json'),
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

function Test-IsHubCodexHookEntry {
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

function Build-CodexHookEntry {
    param([string]$ScriptRelPath, [string]$ExtraArgs)
    $scriptAbs = Join-Path $HubRoot $ScriptRelPath
    $cmd = "`"$PythonExe`" `"$scriptAbs`" $ExtraArgs"
    return [PSCustomObject]@{
        hooks = @(
            [PSCustomObject]@{
                type    = 'command'
                command = $cmd
            }
        )
    }
}

function Remove-TomlSection {
    param([string]$TomlText, [string]$SectionName)
    $lines = $TomlText -split "`r?`n"
    $out = New-Object System.Collections.Generic.List[string]
    $skip = $false
    $targetHeader = "[$SectionName]"
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($trimmed -eq $targetHeader) {
            $skip = $true
            continue
        }
        if ($skip -and $trimmed -match '^\[.+\]$') {
            $skip = $false
        }
        if (-not $skip) {
            $out.Add($line)
        }
    }
    return ($out -join "`n").TrimEnd()
}

function Get-TomlSectionUrl {
    param([string]$TomlText, [string]$SectionName)
    $lines = $TomlText -split "`r?`n"
    $inSection = $false
    $targetHeader = "[$SectionName]"
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if ($trimmed -eq $targetHeader) { $inSection = $true; continue }
        if ($inSection -and $trimmed -match '^\[.+\]$') { break }
        if ($inSection -and $trimmed -match '^url\s*=\s*"([^"]+)"') {
            return $Matches[1]
        }
    }
    return $null
}

$codexCmd = Get-Command codex -ErrorAction SilentlyContinue
if ($codexCmd) {
    $ver = (& codex --version 2>$null) -join ' '
    Write-Host "Detected Codex CLI: $ver"
} else {
    Write-Host "Codex CLI not found on PATH (configuring '$ConfigTomlPath' and '$HooksPath' anyway)."
}

if ($Uninstall) {
    Write-Host "Uninstalling AI Memory Hub Codex CLI entries..."
    if (Test-Path -LiteralPath $ConfigTomlPath) {
        Backup-FileIfExists -Path $ConfigTomlPath
        $toml = Get-Content -LiteralPath $ConfigTomlPath -Raw -Encoding UTF8
        $toml = Remove-TomlSection -TomlText $toml -SectionName 'mcp_servers.ai-memory-codebase'
        $toml = Remove-TomlSection -TomlText $toml -SectionName 'mcp_servers.ai-memory-bridge'
        $finalToml = if ([string]::IsNullOrWhiteSpace($toml)) { '' } else { "$toml`n" }
        Write-TextAtomic -Path $ConfigTomlPath -Content $finalToml
    }
    if (Test-Path -LiteralPath $HooksPath) {
        Backup-FileIfExists -Path $HooksPath
        $hooksRoot = Read-JsonObject -Path $HooksPath
        $hooksProp = $hooksRoot.PSObject.Properties['hooks']
        if ($null -ne $hooksProp -and $null -ne $hooksProp.Value) {
            foreach ($evName in @('SessionStart', 'Stop', 'SessionEnd')) {
                $evProp = $hooksProp.Value.PSObject.Properties[$evName]
                if ($null -ne $evProp -and $null -ne $evProp.Value) {
                    $filtered = @($evProp.Value | Where-Object { -not (Test-IsHubCodexHookEntry $_) })
                    $hooksProp.Value | Add-Member -MemberType NoteProperty -Name $evName -Value $filtered -Force
                }
            }
        }
        Write-TextAtomic -Path $HooksPath -Content ($hooksRoot | ConvertTo-Json -Depth 12)
    }
    Write-Host "Codex CLI uninstall complete."
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

$existingToml = if (Test-Path -LiteralPath $ConfigTomlPath) { Get-Content -LiteralPath $ConfigTomlPath -Raw -Encoding UTF8 } else { '' }
$desiredTomlServers = [ordered]@{
    'mcp_servers.ai-memory-codebase' = @{ url = $CodebaseUrl; env_var = 'AI_MEMORY_CODEBASE_TOKEN' }
    'mcp_servers.ai-memory-bridge'   = @{ url = $BridgeUrl; env_var = 'AI_MEMORY_BRIDGE_TOKEN' }
}

foreach ($secName in $desiredTomlServers.Keys) {
    $existingUrl = Get-TomlSectionUrl -TomlText $existingToml -SectionName $secName
    $targetUrl = $desiredTomlServers[$secName].url
    if ($null -ne $existingUrl -and $existingUrl -ne $targetUrl -and -not $Force) {
        throw "Conflict detected for Codex MCP server '[$secName]' in '$ConfigTomlPath' (existing: $existingUrl, desired: $targetUrl). Re-run with -Force to override."
    }
    $existingToml = Remove-TomlSection -TomlText $existingToml -SectionName $secName
}

$sb = New-Object System.Text.StringBuilder
if (-not [string]::IsNullOrWhiteSpace($existingToml)) {
    [void]$sb.AppendLine($existingToml.TrimEnd())
    [void]$sb.AppendLine('')
}
foreach ($secName in $desiredTomlServers.Keys) {
    $entry = $desiredTomlServers[$secName]
    [void]$sb.AppendLine("[$secName]")
    [void]$sb.AppendLine("url = `"$($entry.url)`"")
    [void]$sb.AppendLine("bearer_token_env_var = `"$($entry.env_var)`"")
    [void]$sb.AppendLine('')
}

$hooksRoot = Read-JsonObject -Path $HooksPath
$hooksObj = Ensure-Prop -Obj $hooksRoot -Name 'hooks' -DefaultValue (New-Object PSObject)
$desiredHooks = [ordered]@{
    'SessionStart' = Build-CodexHookEntry -ScriptRelPath 'integrations\claude\hooks\session_start.py' -ExtraArgs '--source-agent codex'
    'Stop'         = Build-CodexHookEntry -ScriptRelPath 'integrations\claude\hooks\session_event.py' -ExtraArgs '--kind stop --source-agent codex'
    'SessionEnd'   = Build-CodexHookEntry -ScriptRelPath 'integrations\claude\hooks\session_event.py' -ExtraArgs '--kind session_end --source-agent codex'
}
foreach ($evName in $desiredHooks.Keys) {
    $existingArr = Ensure-Prop -Obj $hooksObj -Name $evName -DefaultValue @()
    $kept = @($existingArr | Where-Object { -not (Test-IsHubCodexHookEntry $_) })
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

Backup-FileIfExists -Path $ConfigTomlPath
Write-TextAtomic -Path $ConfigTomlPath -Content $sb.ToString()

Backup-FileIfExists -Path $HooksPath
Write-TextAtomic -Path $HooksPath -Content ($hooksRoot | ConvertTo-Json -Depth 12)

Write-Host "AI Memory Hub Codex CLI integration completed (DryRun=$DryRun)."
Write-Host "  TOML   : $ConfigTomlPath (uses env vars AI_MEMORY_CODEBASE_TOKEN, AI_MEMORY_BRIDGE_TOKEN)"
Write-Host "  Hooks  : $HooksPath (run '/hooks' in Codex to trust new hook commands)"
Write-Host "  Secrets: $EnvFilePath (tokens provisioned without printing)"
