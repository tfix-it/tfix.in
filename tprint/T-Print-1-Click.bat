@echo off
rem T FIX CUSAT - T Print one click. Double-click on Shop PC 1.
rem Installs any update zip, prepares Python, starts T Print and opens the admin page.
title T Print - one click
cd /d "%~dp0"

rem Needs administrator rights (auto-start task, port 443). Ask once, then run again elevated.
net session >nul 2>&1
if errorlevel 1 (
  echo Asking Windows for administrator permission...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\one-click.ps1"
echo.
echo You can close this window.
pause
