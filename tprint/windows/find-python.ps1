<#
  Shared by one-click.ps1 and setup.ps1 (dot-sourced). ASCII only.
  T Print's pinned packages (pymupdf 1.24, pywin32 306, fastapi 0.115) have ready-made
  Windows downloads only up to Python 3.12. On 3.13/3.14 pip tries to compile them and
  fails ("Unable to find Visual Studio"). So T Print always uses Python 3.12 (3.10/3.11 ok).
#>
$script:PyOk = '^3\.(10|11|12)\.'
$script:PyCode = 'import sys; print(sys.version.split()[0])'

function Get-PyVersion([string]$exe, [string[]]$pre) {
    $ErrorActionPreference = 'Continue'   # a warning on stderr must not stop the caller's script
    try {
        if (-not ((Test-Path -LiteralPath $exe) -or (Get-Command $exe -ErrorAction SilentlyContinue))) { return $null }
        $a = @($pre) + @('-c', $script:PyCode)
        $v = (& $exe @a 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and "$v" -match '^3\.\d+\.\d+') { return "$v".Trim() }
    } catch { }
    return $null
}

# Returns @{ Exe = ...; Pre = @(...); Version = '3.12.x' } for a Python T Print can use, or $null.
function Find-SupportedPython {
    $ErrorActionPreference = 'Continue'
    $cands = @(
        @{ Exe = 'py.exe'; Pre = @('-3.12') },
        @{ Exe = 'py.exe'; Pre = @('-V:3.12') },
        @{ Exe = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"; Pre = @() },
        @{ Exe = "$env:ProgramFiles\Python312\python.exe"; Pre = @() },
        @{ Exe = "$env:LOCALAPPDATA\Python\pythoncore-3.12-64\python.exe"; Pre = @() },
        @{ Exe = 'py.exe'; Pre = @('-3.11') },
        @{ Exe = 'py.exe'; Pre = @('-3.10') },
        @{ Exe = 'python.exe'; Pre = @() }
    )
    foreach ($c in $cands) {
        $v = Get-PyVersion $c.Exe $c.Pre
        if ($v -and $v -match $script:PyOk) { return @{ Exe = $c.Exe; Pre = $c.Pre; Version = $v } }
    }
    return $null
}

# Installs Python 3.12: the Python install manager ("py install") if present, else winget.
function Install-Python312 {
    $ErrorActionPreference = 'Continue'
    Write-Host '    Installing Python 3.12 (T Print needs exactly this version; a few minutes)...' -ForegroundColor White
    $done = $false
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        try { & py.exe install 3.12 2>&1 | Out-Host; if ($LASTEXITCODE -eq 0) { $done = $true } } catch { }
    }
    if (-not $done -and (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        try { & winget.exe install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent 2>&1 | Out-Host } catch { }
    }
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
    return (Find-SupportedPython)
}
