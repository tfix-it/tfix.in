<#
  Removes the "T Print Server" scheduled task and stops the watchdog and server.
  Run in PowerShell as administrator:
      powershell -ExecutionPolicy Bypass -File .\windows\uninstall-autostart.ps1
#>
#Requires -RunAsAdministrator
$root = Split-Path -Parent $PSScriptRoot
Stop-ScheduledTask -TaskName 'T Print Server' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'T Print Server' -Confirm:$false -ErrorAction SilentlyContinue

$pidFile = Join-Path $root 'logs\tprint.pid'
if (Test-Path $pidFile) {
    Stop-Process -Id ([int](Get-Content $pidFile | Select-Object -First 1)) -Force -ErrorAction SilentlyContinue
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
Write-Host "Removed. T Print no longer starts automatically."
