<#
.SYNOPSIS
Optional, reversible current-user Windows Login Auto-Start for AI Memory Hub (OFF by default).
Never stores credentials in scheduled-task arguments; never modifies system-wide privileges or deletes data.
#>
[CmdletBinding()]
param(
    [switch]$Enable,
    [switch]$Disable,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$TaskName = 'AIMemoryHub-UserStartup'
$HubRoot = Split-Path $PSScriptRoot -Parent
$Launcher = Join-Path $PSScriptRoot 'Start-AIMemoryHub.ps1'

if ($Status -or (!$Enable -and !$Disable)) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Write-Host "[ENABLED] Scheduled Task '$TaskName' đang ở trạng thái: $($t.State)" -ForegroundColor Green
    } else {
        Write-Host "[DISABLED] Tự khởi động cùng Windows đang TẮT (mặc định). Dùng -Enable để bật." -ForegroundColor Yellow
    }
    exit 0
}

if ($Disable) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[OK] Đã tắt tự khởi động cùng Windows (gỡ '$TaskName'). Toàn bộ dữ liệu dự án được giữ nguyên." -ForegroundColor Green
    } else {
        Write-Host "[OK] Tự khởi động cùng Windows vốn đang tắt." -ForegroundColor Green
    }
    exit 0
}

if ($Enable) {
    if (!(Test-Path -LiteralPath $Launcher -PathType Leaf)) {
        throw "Không tìm thấy launcher: $Launcher"
    }
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`"" -WorkingDirectory $HubRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'AI Memory Hub optional current-user startup (P16B.4)' -Force | Out-Null
    Write-Host "[OK] Đã bật tự khởi động cùng Windows (least-privilege current-user, không chứa secret). Gỡ bằng -Disable." -ForegroundColor Green
}
