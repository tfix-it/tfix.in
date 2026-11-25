@echo off
rem T FIX CUSAT - T Print SETUP for Shop PC 1. Double-click it (from the extracted setup folder).
rem Finds or installs T Print, checks Python, SumatraPDF and the printers, then updates and starts T Print.
title T Print - setup
cd /d "%~dp0"
if not exist "%~dp0windows\setup.ps1" (
  echo Please extract the whole zip first: right-click it, Extract All, then run T-Print-Setup.bat from the extracted folder.
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  echo Asking Windows for administrator permission...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\setup.ps1"
echo.
echo You can close this window.
pause
