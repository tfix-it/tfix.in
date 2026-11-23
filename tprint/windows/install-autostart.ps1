<#
  Run ONCE on Shop PC 1, in PowerShell opened with "Run as administrator",
  while signed in as the Windows user that normally uses the printers:

      powershell -ExecutionPolicy Bypass -File .\windows\install-autostart.ps1

  Creates the scheduled task "T Print Server": at every sign-in of this user it
  starts watchdog.ps1 (hidden), which starts T Print and restarts it if it
  stops. Remove it again with uninstall-autostart.ps1.
#>
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$watchdog = Join-Path $PSScriptRoot 'watchdog.ps1'
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root 'venv\Scripts\python.exe'))) {
    throw "venv not found in $root — finish the T Print setup (README step 2) first."
}
if (-not (Test-Path (Join-Path $root 'tprint.env'))) {
    Write-Warning "No tprint.env yet: online orders stay off until you create it (copy tprint.env.example)."
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdog`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
# Runs in the signed-in user's session so T Print sees the same printers.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName 'T Print Server' -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description 'Starts T Print at sign-in and restarts it if it stops (watchdog.ps1).' | Out-Null
Start-ScheduledTask -TaskName 'T Print Server'

Write-Host "Done. 'T Print Server' starts at every sign-in of $user and is running now."
Write-Host "Logs: $root\logs\  (watchdog.log, tprint-YYYYMMDD.log)"
