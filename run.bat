@echo off
cd /d "%~dp0"
title Dual Sleeper (Portable)

echo ==================================================
echo  Dual Sleeper - Standalone Portable Launcher
echo ==================================================
echo.

if not exist "config.json" (
    if exist "config.json.example" (
        echo Creating config.json from example...
        copy config.json.example config.json >nul
    )
)

echo Starting Dual Sleeper with embedded Python...
echo ==================================================
echo.

.\python_embed\python.exe dual_sleeper.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Application exited with code %errorlevel%.
)

echo.
pause
