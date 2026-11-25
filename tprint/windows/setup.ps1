<#
  T Print - SETUP for Shop PC 1 (started by "T-Print-Setup.bat" in the setup package).

  1. Finds the T Print folder on this PC (or installs T Print fresh in C:\TPrint).
  2. Checks Python, SumatraPDF, LibreOffice and the printers; installs Python and
     SumatraPDF with winget if they are missing.
  3. Copies the update zip, T-Print-1-Click.bat and the windows folder into the
     T Print folder and runs the one-click update/start (windows\one-click.ps1).
  A report is saved on the Desktop: T-Print-setup-report.txt (no passwords or keys in it).
  Written for Windows PowerShell 5.1. ASCII only.
#>
$ErrorActionPreference = 'Stop'
$Pkg = Split-Path -Parent $PSScriptRoot
$Desktop = [Environment]::GetFolderPath('Desktop')
$Report = Join-Path $Desktop 'T-Print-setup-report.txt'
try { Start-Transcript -Path $Report -Force | Out-Null } catch { }

function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c }
function Step([string]$m) { Write-Host ''; Write-Host "== $m" -ForegroundColor Cyan }
function Done([int]$code) {
    Write-Host ''
    Say "Report saved on the Desktop: $Report" 'DarkGray'
    try { Stop-Transcript | Out-Null } catch { }
    exit $code
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
$HasWinget = [bool](Get-Command winget.exe -ErrorAction SilentlyContinue)
function Winget-Install([string]$id, [string]$label) {
    if (-not $HasWinget) { return $false }
    Say "    Installing $label with winget (a few minutes)..." 'White'
    & winget.exe install -e --id $id --accept-package-agreements --accept-source-agreements --silent | Out-Host
    Refresh-Path
    return $true
}

Write-Host ''
Write-Host '  T FIX(R) CUSAT  -  T Print setup for Shop PC 1' -ForegroundColor Yellow
Say ("  PC: {0}   Windows user: {1}   {2}" -f $env:COMPUTERNAME, $env:USERNAME, (Get-Date -Format 'dd MMM yyyy HH:mm'))

$zip = Get-ChildItem -LiteralPath $Pkg -Filter 'tprint-server*.zip' -File | Sort-Object Name -Descending | Select-Object -First 1
if (-not $zip) { Say 'STOPPED: tprint-server zip is missing from the setup folder. Extract the whole setup zip first.' 'Red'; Done 1 }
Say "  Package: $($zip.Name)"

# ------------------------------------------------------------------ 1. T Print folder
Step '1. Looking for the T Print folder on this PC'
$skip = '\\(Windows|Program Files|Program Files \(x86\)|ProgramData|AppData|\$Recycle\.Bin|backup|node_modules|venv)(\\|$)'
$roots = @($Desktop, [Environment]::GetFolderPath('MyDocuments'), (Join-Path $env:USERPROFILE 'Downloads'), $env:USERPROFILE,
           'C:\', 'D:\', 'E:\') | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique
$found = @()
foreach ($r in $roots) {
    $dirs = @(Get-Item -LiteralPath $r) + @(Get-ChildItem -LiteralPath $r -Directory -Depth 2 -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notmatch $skip })
    foreach ($d in $dirs) {
        $p = $d.FullName
        if ($p.StartsWith($Pkg, [StringComparison]::OrdinalIgnoreCase)) { continue }
        if ((Test-Path -LiteralPath (Join-Path $p 'app\main.py')) -and (Test-Path -LiteralPath (Join-Path $p 'static\admin'))) {
            $db = Join-Path $p 'printshop.db'
            $score = 0
            if (Test-Path -LiteralPath $db) { $score += 4 }
            if (Test-Path -LiteralPath (Join-Path $p 'tprint.env')) { $score += 2 }
            if (Test-Path -LiteralPath (Join-Path $p 'venv\Scripts\python.exe')) { $score += 1 }
            $t = if (Test-Path -LiteralPath $db) { (Get-Item -LiteralPath $db).LastWriteTime } else { $d.LastWriteTime }
            $found += [pscustomobject]@{ Path = $p; Score = $score; When = $t }
        }
    }
}
$found = @($found | Sort-Object Path -Unique | Sort-Object @{ e = 'Score'; Descending = $true }, @{ e = 'When'; Descending = $true })
foreach ($f in $found) {
    $what = @()
    if ($f.Score -band 4) { $what += 'database' }
    if ($f.Score -band 2) { $what += 'tprint.env' }
    if ($f.Score -band 1) { $what += 'venv' }
    Say ("    found: {0}  ({1})" -f $f.Path, ($(if ($what) { $what -join ', ' } else { 'files only' })))
}
$Target = $null
if ($found.Count -and $found[0].Score -gt 0) {
    $Target = $found[0].Path
    Say "    Using: $Target" 'Green'
    if ($found.Count -gt 1) { Say '    (The other copies above are left as they are.)' 'DarkGray' }
} else {
    $Target = 'C:\TPrint'
    Say "    No working T Print folder found - installing a fresh copy in $Target" 'Yellow'
    if (-not (Test-Path -LiteralPath (Join-Path $Target 'app\main.py'))) {
        $tmp = Join-Path $env:TEMP ('tprint-setup-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $zip.FullName -DestinationPath $tmp -Force
        $src = Join-Path $tmp 'tprint'
        New-Item -ItemType Directory -Force -Path $Target | Out-Null
        Copy-Item -Path (Join-Path $src '*') -Destination $Target -Recurse -Force
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ------------------------------------------------------------------ 2. Python
Step '2. Python'
function Test-Python {
    $code = 'import sys; print(sys.version.split()[0])'
    $v = $null
    try { if (Get-Command py.exe -ErrorAction SilentlyContinue) { $v = (& py.exe -3 -c $code 2>$null | Select-Object -First 1) } } catch { }
    if ("$v" -match '^3\.\d+') { return $v }
    $v = $null
    try { if (Get-Command python.exe -ErrorAction SilentlyContinue) { $v = (& python.exe -c $code 2>$null | Select-Object -First 1) } } catch { }
    if ("$v" -match '^3\.\d+') { return $v }
    return $null
}
$venvPy = Join-Path $Target 'venv\Scripts\python.exe'
$pyv = Test-Python
if (Test-Path -LiteralPath $venvPy) {
    Say "    T Print's own Python (venv) is there." 'Green'
} elseif ($pyv) {
    Say "    Python $pyv is installed." 'Green'
} else {
    Say '    Python is not installed (the "python" in the Microsoft Store shortcut does not count).' 'Yellow'
    if (Winget-Install 'Python.Python.3.12' 'Python 3.12') { $pyv = Test-Python }
    if ($pyv) { Say "    Python $pyv installed." 'Green' }
    else {
        Say '    Please install Python 3.12 from python.org: tick "Add python.exe to PATH", then run T-Print-Setup.bat again.' 'Red'
        Start-Process 'https://www.python.org/downloads/windows/'
        Done 1
    }
}

# ------------------------------------------------------------------ 3. SumatraPDF / LibreOffice
Step '3. SumatraPDF (sends files to the printer) and LibreOffice (Word/PowerPoint)'
function Find-Sumatra {
    $p = @("$env:ProgramFiles\SumatraPDF\SumatraPDF.exe", "$env:LOCALAPPDATA\SumatraPDF\SumatraPDF.exe",
           "${env:ProgramFiles(x86)}\SumatraPDF\SumatraPDF.exe", (Join-Path $Target 'SumatraPDF.exe'),
           (Join-Path $Target 'tools\SumatraPDF.exe'))
    return ($p | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1)
}
$sum = Find-Sumatra
if (-not $sum) {
    Say '    SumatraPDF not found.' 'Yellow'
    if (Winget-Install 'SumatraPDF.SumatraPDF' 'SumatraPDF') { $sum = Find-Sumatra }
}
if ($sum) { Say "    SumatraPDF: $sum" 'Green' }
else {
    Say '    SumatraPDF is still missing: nothing can print. Install it from sumatrapdfreader.org, then run this again.' 'Red'
    Start-Process 'https://www.sumatrapdfreader.org/download-free-pdf-viewer'
}
$lo = @("$env:ProgramFiles\LibreOffice\program\soffice.exe", "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe") |
    Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if ($lo) { Say "    LibreOffice: $lo" 'Green' }
else { Say '    LibreOffice: not installed. PDFs and photos print fine; Word/PowerPoint orders need it (libreoffice.org, free).' 'Yellow' }

# ------------------------------------------------------------------ 4. printers
Step '4. Printers'
try {
    $pr = @(Get-Printer -ErrorAction Stop | Where-Object { $_.Name -notmatch 'Microsoft Print to PDF|Microsoft XPS|OneNote|Fax' })
    $def = (Get-CimInstance -ClassName Win32_Printer -Filter 'Default=TRUE' -ErrorAction SilentlyContinue).Name
    if ($pr.Count) {
        foreach ($p in $pr) { Say ("    - {0}  [{1}]{2}" -f $p.Name, $p.PrinterStatus, $(if ($p.Name -eq $def) { '  (default)' } else { '' })) 'White' }
    } else {
        Say '    No printer installed in Windows. Add it in Settings > Bluetooth & devices > Printers & scanners and print a test page.' 'Red'
    }
} catch { Say '    Could not list printers.' 'Yellow' }

# ------------------------------------------------------------------ 5. online orders key
Step '5. Online orders (shop key in tprint.env)'
$envFile = Join-Path $Target 'tprint.env'
if ((Test-Path -LiteralPath $envFile) -and ((Get-Content -LiteralPath $envFile -Raw) -match 'PRINTSHOP_CLOUD_TOKEN\s*=\s*\S{20,}') -and
    -not ((Get-Content -LiteralPath $envFile -Raw) -match 'PASTE_THE_SHOP_TOKEN_HERE')) {
    Say '    Shop key is set.' 'Green'
} else {
    Say '    Shop key is not set yet: Notepad will open in the next part - paste the key after PRINTSHOP_CLOUD_TOKEN= and save.' 'Yellow'
}

# ------------------------------------------------------------------ 6. install + start
Step "6. Installing the update and starting T Print in $Target"
Copy-Item -LiteralPath $zip.FullName -Destination (Join-Path $Target $zip.Name) -Force
(Get-Item -LiteralPath (Join-Path $Target $zip.Name)).LastWriteTime = Get-Date
Copy-Item -LiteralPath (Join-Path $Pkg 'T-Print-1-Click.bat') -Destination $Target -Force
& robocopy.exe (Join-Path $Pkg 'windows') (Join-Path $Target 'windows') /E /NFL /NDL /NJH /NJS /NP | Out-Null
try { Stop-Transcript | Out-Null } catch { }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Target 'windows\one-click.ps1')
$rc = $LASTEXITCODE
try { Start-Transcript -Path $Report -Append | Out-Null } catch { }
Write-Host ''
if ($rc -eq 0) { Say "  All set. From now on use T-Print-1-Click.bat in $Target (a shortcut is on the Desktop)." 'Green' }
else { Say "  The start did not finish. Send the report file to Claude: $Report and $Target\logs\one-click.log" 'Red' }
try {
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $Desktop 'T Print 1-Click.lnk'))
    $lnk.TargetPath = Join-Path $Target 'T-Print-1-Click.bat'
    $lnk.WorkingDirectory = $Target
    $lnk.Save()
} catch { }
Done $rc
