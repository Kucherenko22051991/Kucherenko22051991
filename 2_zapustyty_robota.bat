@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
where python >nul 2>nul || (echo Python не знайдено. Встановіть його з python.org, поставивши галочку "Add python.exe to PATH". & pause & exit /b 1)
title OMNIS robot
:loop
python -m omnis.strategies.autotrader --live --loop
echo Робот зупинився. Перезапуск через 60 секунд. Щоб вимкнути робота, закрийте це вікно.
timeout /t 60 /nobreak >nul
goto loop
