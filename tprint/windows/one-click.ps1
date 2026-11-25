<#
  T Print - ONE CLICK for Shop PC 1.
  Started by "T-Print-1-Click.bat" (double-click it; it asks for administrator permission).

  What it does, in order:
    1. If a newer "tprint-server*.zip" is in this folder or in Downloads, backs up the
       current app\, static\, windows\ folders and installs the update.
       Never touches: printshop.db, uploads\, cert.pem/key.pem, app\config.py, tprint.env.
    2. Makes sure Python and the venv are ready and installs/updates the requirements.
    3. Makes sure tprint.env (the shop token for online orders) exists.
    4. Installs the "T Print Server" auto-start task if missing, and (re)starts T Print.
    5. Waits until T Print answers, opens the admin page, and checks print.tfix.in
       shows the server online.
  Everything is logged to logs\one-click.log.
  Written for Windows PowerShell 5.1 (built into Windows 10/11).
#>
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
$Logs = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
try { Start-Transcript -Path (Join-Path $Logs 'one-click.log') -Append | Out-Null } catch { }

function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Step([string]$n, [string]$m) { Write-Host ''; Write-Host "[$n] $m" -ForegroundColor Cyan }
function Fail([string]$m) {
    Write-Host ''
    Write-Host "STOPPED: $m" -ForegroundColor Red
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

Write-Host ''
Write-Host '  T FIX(R) CUSAT  -  T Print one-click start' -ForegroundColor Yellow
Write-Host "  Folder: $Root" -ForegroundColor DarkGray

if (-not (Test-Path (Join-Path $Root 'app\main.py'))) {
    Fail "This doesn't look like the T Print folder (app\main.py is missing). Put T-Print-1-Click.bat in the T Print folder."
}

# ---------------------------------------------------------------- 1. update
Step 1 'Checking for a T Print update (tprint-server*.zip)'
$downloads = Join-Path $env:USERPROFILE 'Downloads'
$zip = Get-ChildItem -Path @($Root, $downloads) -Filter 'tprint-server*.zip' -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$marker = Join-Path $Logs 'last-update.txt'
if (-not $zip) {
    Say '    No update zip found - keeping the current version.'
} else {
    $stamp = '{0}|{1}|{2}' -f $zip.Name, $zip.Length, $zip.LastWriteTimeUtc.Ticks
    $done = if (Test-Path $marker) { (Get-Content -LiteralPath $marker -Raw).Trim() } else { '' }
    if ($done -eq $stamp) {
        Say "    $($zip.Name) is already installed."
    } else {
        Say "    Installing $($zip.FullName)" 'White'
        $tmp = Join-Path $env:TEMP ('tprint-update-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $zip.FullName -DestinationPath $tmp -Force
        $src = Join-Path $tmp 'tprint'
        if (-not (Test-Path (Join-Path $src 'app\main.py'))) { $src = $tmp }
        if (-not (Test-Path (Join-Path $src 'app\main.py'))) {
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            Fail "$($zip.Name) does not contain a T Print app folder. Nothing was changed."
        }

        # Stop T Print first so no file is in use (the watchdog starts it again in step 4).
        $pidFile = Join-Path $Logs 'tprint.pid'
        if (Test-Path $pidFile) {
            $old = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($old) { Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue }
        }

        $backup = Join-Path $Root ('backup\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        foreach ($f in @('app', 'static', 'windows')) {
            $p = Join-Path $Root $f
            if (Test-Path $p) { Copy-Item -LiteralPath $p -Destination $backup -Recurse -Force }
        }
        Say "    Backup of the old version: $backup"

        foreach ($f in @('app', 'static', 'windows')) {
            $from = Join-Path $src $f
            if (-not (Test-Path $from)) { continue }
            $robo = @($from, (Join-Path $Root $f), '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD', '__pycache__')
            if ($f -eq 'app' -and (Test-Path (Join-Path $Root 'app\config.py'))) { $robo += @('/XF', 'config.py') }  # keep the shop's settings
            & robocopy.exe @robo | Out-Null
            if ($LASTEXITCODE -ge 8) { Fail "Copying $f failed (robocopy code $LASTEXITCODE). The old version is in $backup." }
        }
        foreach ($f in @('requirements.txt', 'requirements-mcp.txt', 'tprint.env.example', 'START_HERE.md', 'README.md', 'CHANGELOG_MCP.md')) {
            $from = Join-Path $src $f
            if (Test-Path $from) { Copy-Item -LiteralPath $from -Destination (Join-Path $Root $f) -Force }
        }
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Set-Content -LiteralPath $marker -Value $stamp
        Say '    Update installed. Kept: printshop.db, uploads, certificates, app\config.py, tprint.env.' 'Green'
    }
}

# ---------------------------------------------------------------- 2. python
Step 2 'Checking Python and the T Print environment (venv)'
$py = Join-Path $Root 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Say '    No venv yet - creating it.'
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3 -m venv (Join-Path $Root 'venv')
    } elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -m venv (Join-Path $Root 'venv')
    } else {
        Fail 'Python is not installed. Install Python 3.12 from python.org (tick "Add python.exe to PATH"), then double-click T-Print-1-Click.bat again.'
    }
    if (-not (Test-Path $py)) { Fail 'Could not create the venv. See logs\one-click.log.' }
}
Say '    Installing/updating the required packages (needs internet; skips if offline)...'
& $py -m pip install -r (Join-Path $Root 'requirements.txt') --disable-pip-version-check -q
if ($LASTEXITCODE -ne 0) {
    Say '    Could not update packages (offline?). Continuing with what is installed.' 'Yellow'
} else {
    Say '    Packages OK.' 'Green'
}
foreach ($c in @('cert.pem', 'key.pem')) {
    if (-not (Test-Path (Join-Path $Root $c))) {
        Say "    Note: $c is missing. T Print uses HTTPS on port 443 and needs cert.pem and key.pem (see README)." 'Yellow'
    }
}

# ---------------------------------------------------------------- 3. online orders
Step 3 'Checking online orders (tprint.env)'
$envFile = Join-Path $Root 'tprint.env'
if (-not (Test-Path $envFile)) {
    Copy-Item -LiteralPath (Join-Path $Root 'tprint.env.example') -Destination $envFile
    Say '    Created tprint.env. Notepad will open: paste the shop token after PRINTSHOP_CLOUD_TOKEN=, save, close.' 'Yellow'
    Start-Process notepad.exe -ArgumentList "`"$envFile`"" -Wait
}
$envText = Get-Content -LiteralPath $envFile -Raw
$onlineOn = ($envText -match 'PRINTSHOP_CLOUD_TOKEN\s*=\s*\S{20,}') -and ($envText -notmatch 'PASTE_THE_SHOP_TOKEN_HERE')
if ($onlineOn) { Say '    Online orders: ON (token found).' 'Green' }
else { Say '    Online orders: OFF - tprint.env has no shop token yet. T Print still works in the shop.' 'Yellow' }
$port = 443
if ($envText -match '(?m)^\s*PRINTSHOP_PORT\s*=\s*(\d+)') { $port = [int]$Matches[1] }

# ---------------------------------------------------------------- 3b. printing tools
Step '3b' 'Checking SumatraPDF (sends files to the printer), LibreOffice (Word/PowerPoint) and printers'
function Set-EnvLine([string]$name, [string]$value) {
    $lines = @(Get-Content -LiteralPath $envFile | Where-Object { $_ -notmatch ('^\s*#?\s*' + $name + '\s*=') })
    $lines += ($name + '=' + $value)
    Set-Content -LiteralPath $envFile -Value $lines -Encoding ASCII
}
function Find-Tool([string]$name, [string[]]$places, [string]$label, [string]$help) {
    $cur = ''
    if ($envText -match ('(?m)^\s*' + $name + '\s*=\s*(.+?)\s*$')) { $cur = $Matches[1].Trim('"') }
    if ($cur -and (Test-Path -LiteralPath $cur)) { Say "    $label OK: $cur" 'Green'; return }
    $found = $places | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if ($found) {
        Set-EnvLine $name $found
        Say "    $label found: $found (saved in tprint.env)" 'Green'
    } else {
        Say "    $label NOT found. $help" 'Yellow'
    }
}
Find-Tool 'SUMATRA_PATH' @(
    "$env:ProgramFiles\SumatraPDF\SumatraPDF.exe",
    "$env:LOCALAPPDATA\SumatraPDF\SumatraPDF.exe",
    "${env:ProgramFiles(x86)}\SumatraPDF\SumatraPDF.exe",
    (Join-Path $Root 'SumatraPDF.exe'),
    (Join-Path $Root 'tools\SumatraPDF.exe')
) 'SumatraPDF' 'Nothing can print until it is installed: sumatrapdfreader.org (or put SumatraPDF.exe in this T Print folder).'
Find-Tool 'LIBREOFFICE_PATH' @(
    "$env:ProgramFiles\LibreOffice\program\soffice.exe",
    "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe"
) 'LibreOffice' 'PDF and photos still print; Word/PowerPoint files need LibreOffice (libreoffice.org).'
$envText = Get-Content -LiteralPath $envFile -Raw
try {
    $printers = @(Get-Printer -ErrorAction Stop | Where-Object { $_.Name -notmatch 'Microsoft Print to PDF|Microsoft XPS|OneNote|Fax' })
    if ($printers.Count) {
        Say '    Printers on this PC (T Print shows these in the admin page):'
        foreach ($p in $printers) { Say ("      - {0}  [{1}]" -f $p.Name, $p.PrinterStatus) 'White' }
    } else {
        Say '    No real printer found. Install the printer in Windows Settings > Printers & scanners first.' 'Yellow'
    }
} catch { Say '    Could not list printers (Get-Printer not available).' 'Yellow' }

# ---------------------------------------------------------------- 4. start
Step 4 'Starting T Print (auto-start at every sign-in)'
$task = Get-ScheduledTask -TaskName 'T Print Server' -ErrorAction SilentlyContinue
if (-not $task) {
    Say '    Installing the "T Print Server" auto-start task for this Windows user...'
    & (Join-Path $PSScriptRoot 'install-autostart.ps1')
} else {
    # Restart so the newest code runs: stop the server; the watchdog (or the task) starts it again.
    $pidFile = Join-Path $Logs 'tprint.pid'
    if (Test-Path $pidFile) {
        $old = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($old) { Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue }
    }
    Start-ScheduledTask -TaskName 'T Print Server'
    Say '    Auto-start task found and started.'
}

# ---------------------------------------------------------------- 5. check
Step 5 "Waiting for T Print to answer on port $port (up to 2 minutes)"
$up = $false
for ($i = 0; $i -lt 60 -and -not $up; $i++) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { $t = $c.ConnectAsync('127.0.0.1', $port); $up = $t.Wait(1500) -and $c.Connected } catch { } finally { $c.Dispose() }
    if (-not $up) { Start-Sleep -Seconds 2 }
}
if (-not $up) {
    Fail "T Print did not start. Look at the newest files in $Logs (tprint-*.err.log and watchdog.log)."
}
Say '    T Print is running.' 'Green'
$admin = if ($port -eq 443) { 'https://localhost/admin/' } else { "https://localhost:$port/admin/" }
Start-Process $admin

if ($onlineOn) {
    Say '    Checking that print.tfix.in sees this PC (can take a minute)...'
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
    $seen = $false
    for ($i = 0; $i -lt 9 -and -not $seen; $i++) {
        try {
            $s = Invoke-RestMethod -Uri 'https://print.tfix.in/api/server.php' -TimeoutSec 10
            $seen = [bool]$s.online
        } catch { }
        if (-not $seen) { Start-Sleep -Seconds 10 }
    }
    if ($seen) { Say '    print.tfix.in shows the print server ONLINE.' 'Green' }
    else { Say '    print.tfix.in does not show it online yet. Check the internet and the shop token in tprint.env.' 'Yellow' }
}

Write-Host ''
Write-Host '  DONE. T Print is running and starts by itself whenever this user signs in.' -ForegroundColor Green
Write-Host "  Admin page: $admin" -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch { }
