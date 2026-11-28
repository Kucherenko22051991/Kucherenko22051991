@echo off
chcp 65001 >nul
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\omnis_robot.bat" echo @echo off
>> "%STARTUP%\omnis_robot.bat" echo start "OMNIS robot" /min "%~dp02_zapustyty_robota.bat"
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo Готово. Робот запускатиметься сам щоразу після входу у Windows,
echo а комп'ютер більше не засинатиме.
echo Щоб прибрати автозапуск, видаліть файл omnis_robot.bat з папки автозавантаження (Win+R, shell:startup).
pause
