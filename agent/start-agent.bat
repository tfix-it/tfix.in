@echo off
rem TPrint print agent: connects this shop PC to https://print.tfix.in.
rem Needs TPRINT_AGENT_KEY set once with:  setx TPRINT_AGENT_KEY "<agent_key from tprint-config.php>"
cd /d "%~dp0.."
:loop
"venv-agent\Scripts\python.exe" -m agent.print_agent
echo Agent stopped. Restarting in 10 seconds (close this window to stop it)...
timeout /t 10 /nobreak >nul
goto loop
