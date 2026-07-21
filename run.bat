@echo off
title Dual Sleeper

echo ==================================================
echo  Dual Sleeper - Auto Launcher
echo ==================================================

REM 1. Check Python installation
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH.
    echo Please install Python 3.8+ and try again.
    pause
    exit /b 1
)

REM 2. Create virtual environment (.venv) if not exists
if not exist ".venv" (
    echo [1/3] Creating Python virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [1/3] Virtual environment created successfully.
) else (
    echo [1/3] Virtual environment (.venv) found.
)

REM 3. Activate virtual environment
call .venv\Scripts\activate.bat

REM 4. Install requirements in virtual environment
echo [2/3] Checking and installing dependencies (psutil)...
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install dependencies. Please check internet connection.
    pause
    exit /b 1
)

REM 5. Auto copy config.json from config.json.example if missing
if not exist "config.json" (
    if exist "config.json.example" (
        echo [3/3] Creating config.json from config.json.example...
        copy config.json.example config.json >nul
    )
)

echo [3/3] Launching Dual Sleeper...
echo ==================================================
echo.

.venv\Scripts\python.exe dual_sleeper.py

pause
