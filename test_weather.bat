@echo off
chcp 65001 > nul
title Dual Sleeper - Weather API Test

echo ===================================================
echo   Dual Sleeper 天気API 動作確認テスト
echo ===================================================
echo.

if exist "%~dp0python_embed\python.exe" (
    "%~dp0python_embed\python.exe" "%~dp0test_weather.py"
) else (
    python "%~dp0test_weather.py"
)

pause
