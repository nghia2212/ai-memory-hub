<#
.SYNOPSIS
Idempotent, single-instance launcher for Windows Host Brain Agent (loopback 127.0.0.1:8084).
Checks Python, .env, port ownership (never kills unrelated processes), and startup lock.
#>
[CmdletBinding()]
param(
    [int]$Port = 8084,
    [int]$TimeoutSec = 15,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$HubRoot = Split-Path $PSScriptRoot -Parent
$ScriptPath = Join-Path $HubRoot 'services\orchestrator\host_brain_agent.py'
$DataDir = Join-Path $HubRoot 'data'
$LockFile = Join-Path $DataDir 'host-brain-agent.lock'
$PidFile = Join-Path $DataDir 'host-brain-agent.pid'
$LogOut = Join-Path $DataDir 'host-brain-agent.out.log'
$LogErr = Join-Path $DataDir 'host-brain-agent.err.log'

function Test-HostBrainHealth([int]$P) {
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:$P/healthz" -TimeoutSec 2
        if ($h -and $h.ready -eq $true -and $h.service -eq 'host-brain-agent') {
            return $h
        }
    }
    catch {}
    return $null
}

function Get-PortOccupant([int]$P) {
    try {
        $conn = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $P -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)" -ErrorAction SilentlyContinue
            return [pscustomobject]@{ Pid = $conn.OwningProcess; Name = $proc.Name; CommandLine = $proc.CommandLine }
        }
    }
    catch {}
    return $null
}

# 1. Fast path: already healthy and verified as host-brain-agent
$existing = Test-HostBrainHealth $Port
if ($existing) {
    Write-Host "[OK] Host Brain Agent đang chạy và sẵn sàng tại http://127.0.0.1:$Port (không khởi tạo trùng)." -ForegroundColor Green
    exit 0
}

# 2. Check prerequisites: script, .env, Python
if (!(Test-Path -LiteralPath $ScriptPath -PathType Leaf)) {
    Write-Error "[MISSING_SCRIPT] Không tìm thấy $ScriptPath"
    exit 2
}
$EnvFile = Join-Path $HubRoot '.env'
if (!(Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Write-Error "[MISSING_ENV] Không tìm thấy tệp cấu hình .env tại $EnvFile. Hãy chạy scripts\init-env.ps1 trước."
    exit 3
}
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (!$pyCmd) { $pyCmd = Get-Command py -ErrorAction SilentlyContinue }
if (!$pyCmd) {
    Write-Error "[MISSING_PYTHON] Không tìm thấy Python trên PATH. Hãy cài Python 3.11+ (không tự động cài đặt)."
    exit 4
}

# 3. Check if port is occupied by an unrelated process (NEVER kill unrelated processes!)
$occ = Get-PortOccupant $Port
if ($occ) {
    $isOwned = ($occ.Name -match '^python' -and $occ.CommandLine -match 'host_brain_agent\.py')
    if (!$isOwned) {
        Write-Error "[PORT_OCCUPIED] Cổng 127.0.0.1:$Port đang bị tiến trình khác chiếm (PID=$($occ.Pid), Name=$($occ.Name)). Không dừng tiến trình lạ; hãy giải phóng cổng hoặc cấu hình HOST_BRAIN_PORT."
        exit 5
    }
    Write-Host "[WAIT] Tiến trình host_brain_agent.py (PID=$($occ.Pid)) đang khởi động trên cổng $Port..." -ForegroundColor Yellow
}

if ($DryRun) {
    Write-Host "[DRYRUN] Sẽ khởi động Host Brain Agent bằng '$($pyCmd.Source)' tại $HubRoot (port=$Port)." -ForegroundColor Cyan
    exit 0
}

$null = New-Item -ItemType Directory -Path $DataDir -Force

# 4. Acquire single-instance startup lock (prevents concurrent duplicate spawns)
$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($LockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
}
catch {
    Write-Host "[LOCK] Một phiên khởi động Host Brain Agent khác đang chạy; chờ tiến trình đó sẵn sàng..." -ForegroundColor Yellow
    $deadlineWait = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadlineWait) {
        Start-Sleep -Milliseconds 500
        if (Test-HostBrainHealth $Port) {
            Write-Host "[OK] Host Brain Agent đã sẵn sàng tại http://127.0.0.1:$Port." -ForegroundColor Green
            exit 0
        }
    }
    Write-Error "[LOCK_TIMEOUT] Hết thời gian chờ phiên khởi động Host Brain Agent đang giữ lock."
    exit 6
}

try {
    # Re-check health after acquiring lock
    if (Test-HostBrainHealth $Port) {
        Write-Host "[OK] Host Brain Agent đã sẵn sàng tại http://127.0.0.1:$Port." -ForegroundColor Green
        exit 0
    }

    # Check if an owned python host_brain_agent.py process already exists (avoid duplicate!)
    $ownedProcs = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'host_brain_agent\.py' -and $_.Name -match '^python' })
    if ($ownedProcs.Count -eq 0) {
        $env:HOST_BRAIN_PORT = "$Port"
        $proc = Start-Process -FilePath $pyCmd.Source -ArgumentList @($ScriptPath) -WorkingDirectory $HubRoot -WindowStyle Hidden -RedirectStandardOutput $LogOut -RedirectStandardError $LogErr -PassThru
        Set-Content -LiteralPath $PidFile -Value "$($proc.Id)" -Encoding UTF8
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        if (Test-HostBrainHealth $Port) {
            Write-Host "[OK] Host Brain Agent đã khởi động thành công tại http://127.0.0.1:$Port (ready=True)." -ForegroundColor Green
            exit 0
        }
    }
    $errTail = if (Test-Path -LiteralPath $LogErr) { (Get-Content -LiteralPath $LogErr -Tail 10 -ErrorAction SilentlyContinue) -join "`n" } else { '' }
    Write-Error "[HEALTH_TIMEOUT] Host Brain Agent không phản hồi /healthz sau ${TimeoutSec}s trên cổng $Port. Xem log: $LogErr`n$errTail"
    exit 7
}
finally {
    if ($lockStream) { $lockStream.Dispose() }
}
