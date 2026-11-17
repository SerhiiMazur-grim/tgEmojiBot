@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set PYTHONUTF8=1
for /f %%a in ('echo prompt $E^| cmd') do set "E=%%a"
set "B=%E%[1;97m"
set "W=%E%[97m"
set "P=%E%[94m"
set "G=%E%[92m"
set "R=%E%[91m"
set "N=%E%[0m"
cls
echo %B%Окружение Python%N%
echo %W%Зависимости бота устанавливаются в виртуальное окружение.%N%
echo.
if not exist ".venv\Scripts\python.exe" goto ask
set "PY=.venv\Scripts\python.exe"
echo %G%Найдено окружение .venv.%N%
goto install
:ask
set "ANS="
set /p "ANS=%P%Создать .venv? Enter - создать, n - сделаю сам: %W%"
echo %N%
if /i "%ANS%"=="n" goto own
if /i "%ANS%"=="т" goto own
echo %W%Создание .venv...%N%
py -3.12 -m venv .venv 2>nul || python -m venv .venv
if errorlevel 1 goto fail
set "PY=.venv\Scripts\python.exe"
goto install
:own
set "OWN=%VIRTUAL_ENV%%CONDA_PREFIX%"
if defined OWN goto ownok
echo %W%Создайте и активируйте своё окружение Python 3.12, затем запустите setup_env.bat снова.%N%
pause
exit /b 1
:ownok
set "PY=python"
echo %G%Используется окружение: %OWN%%N%
:install
set TOTAL=0
for /f "usebackq delims=" %%p in ("requirements.txt") do set /a TOTAL+=1
set COUNT=0
echo.
echo %B%Установка пакетов%N%
for /f "usebackq delims=" %%p in ("requirements.txt") do (
    set /a COUNT+=1
    echo %W%[!COUNT!/!TOTAL!] %%p%N%
    "%PY%" -m pip install --disable-pip-version-check -q "%%p"
    if errorlevel 1 goto fail
)
echo %G%Пакеты установлены.%N%
"%PY%" setup_env.py
if errorlevel 1 pause
exit /b %errorlevel%
:fail
echo %R%Установка не удалась. Нужен Python 3.12.%N%
pause
exit /b 1
