@echo off
rem Starts T Print now (same as signing in). Double-click on Shop PC 1.
schtasks /run /tn "T Print Server"
if errorlevel 1 (
  echo The "T Print Server" task is not installed. Run windows\install-autostart.ps1 as administrator first.
) else (
  echo T Print is starting. The website will show "AI Auto Print online" within about a minute.
)
pause
