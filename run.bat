@echo off
title Dual Sleeper Launcher

echo [DEBUG] Launcher started.
echo [DEBUG] Current directory: %CD%

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.8 or higher.
    echo.
    pause
    exit /b 1
)

REM Setup config.json
if not exist "config.json" (
    if exist "config.json.example" (
        echo [INFO] Creating config.json from example...
        copy config.json.example config.json >nul
    )
)

REM Try installing psutil
echo [INFO] Checking dependencies (psutil)...
python -m pip install psutil >nul 2>&1

echo.
echo [INFO] Starting Dual Sleeper script...
echo ==================================================
echo.

python dual_sleeper.py

echo.
echo [INFO] Program finished.
pause
