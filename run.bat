@echo off
cd /d "%~dp0"
title Dual Sleeper

echo ==================================================
echo  Dual Sleeper Launcher
echo ==================================================
echo.

REM 1. Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python was not found on your system!
    echo Please install Python 3.8+ and make sure to check "Add Python to PATH".
    echo.
    pause
    goto :eof
)

REM 2. Clean broken .venv if any
if exist ".venv" (
    if not exist ".venv\Scripts\activate.bat" (
        echo [INFO] Repairing virtual environment...
        rmdir /s /q .venv >nul 2>&1
    )
)

REM 3. Create .venv if not exists
if not exist ".venv" (
    echo Creating virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        echo Running directly with system Python...
        goto :RUN_DIRECT
    )
)

REM 4. Activate .venv
if exist ".venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call .venv\Scripts\activate.bat
)

:RUN_DIRECT
echo Installing required packages (psutil)...
python -m pip install psutil

if not exist "config.json" (
    if exist "config.json.example" (
        echo Creating config.json from example...
        copy config.json.example config.json >nul
    )
)

echo.
echo Starting Dual Sleeper...
echo ==================================================
echo.

python dual_sleeper.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Application stopped unexpectedly with code %errorlevel%.
)

echo.
echo Press any key to exit.
pause
