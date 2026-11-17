@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".env" goto nosetup
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" bot.py
pause
exit /b 0
:nosetup
echo Сначала запустите setup_env.bat.
pause
exit /b 1
