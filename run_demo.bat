@echo off
REM One-click client demo on Windows: simulated scene, dashboard opens in the browser.
cd /d "%~dp0"
python run.py --source sim --profile demo --open %*
