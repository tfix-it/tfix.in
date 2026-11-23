<#
  T Print watchdog — keeps the T Print shop server running on Shop PC 1.

  Started automatically at Windows sign-in by the "T Print Server" scheduled
  task (see install-autostart.ps1). Every minute it checks that the server
  answers on its port; if it doesn't, it starts it (or restarts a frozen one).

  Settings (cloud URL/token, port) are read from ..\tprint.env — see
  tprint.env.example. Logs go to ..\logs\.
#>
param([int]$IntervalSec = 60, [int]$StartupGraceSec = 45)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot                  # the tprint folder
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

function Write-Log([string]$msg) {
    "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg |
        Add-Content -Path (Join-Path $Logs 'watchdog.log') -Encoding UTF8
}

# Only one watchdog at a time.
$mutex = New-Object System.Threading.Mutex($false, 'Global\TPrintWatchdog')
if (-not $mutex.WaitOne(0)) { exit 0 }

# Load KEY=VALUE settings (PRINTSHOP_CLOUD_URL, PRINTSHOP_CLOUD_TOKEN, ...).
$envFile = Join-Path $Root 'tprint.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"', "'"), 'Process')
        }
    }
}
$env:PYTHONUNBUFFERED = '1'

$Python = Join-Path $Root 'venv\Scripts\python.exe'
$Port = if ($env:PRINTSHOP_PORT) { [int]$env:PRINTSHOP_PORT } else { 443 }
$PidFile = Join-Path $Logs 'tprint.pid'

function Test-ServerPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return ($task.Wait(3000) -and $client.Connected)
    } catch { return $false } finally { $client.Dispose() }
}

function Get-OurProcess {
    if (-not (Test-Path $PidFile)) { return $null }
    $id = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $id) { return $null }
    $p = Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
    if ($p -and -not $p.HasExited) { return $p } else { return $null }
}

function Start-TPrint {
    if (-not (Test-Path $Python)) { Write-Log "ERROR: $Python not found — set up the venv first (README step 2)"; return }
    $stamp = Get-Date -Format 'yyyyMMdd'
    $p = Start-Process -FilePath $Python -ArgumentList '-m', 'app.main' -WorkingDirectory $Root `
        -NoNewWindow -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "tprint-$stamp.log") `
        -RedirectStandardError (Join-Path $Logs "tprint-$stamp.err.log")
    Set-Content -Path $PidFile -Value $p.Id
    Write-Log "started T Print (pid $($p.Id), port $Port)"
    Start-Sleep -Seconds $StartupGraceSec
}

Write-Log "watchdog running (checks every $IntervalSec s, port $Port)"
$failures = 0
while ($true) {
    try {
        if (Test-ServerPort) {
            $failures = 0                                   # server answers — all good
        } else {
            $ours = Get-OurProcess
            if (-not $ours) {
                Write-Log 'T Print is not running — starting it'
                Start-TPrint
            } else {
                $failures++
                if ($failures -ge 3) {                      # running but frozen for ~3 minutes
                    Write-Log "T Print (pid $($ours.Id)) not answering — restarting"
                    Stop-Process -Id $ours.Id -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 3
                    Start-TPrint
                    $failures = 0
                }
            }
        }
    } catch {
        Write-Log "watchdog error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $IntervalSec
}
