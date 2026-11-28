@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
where python >nul 2>nul || (echo Python не знайдено. Встановіть його з python.org, поставивши галочку "Add python.exe to PATH". & pause & exit /b 1)
python -m omnis.strategies.autotrader --setup
echo.
pause
