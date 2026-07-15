@echo off
echo ==================================================
echo  Starting Dual Sleeper Script...
echo ==================================================

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python and try again.
    pause
    exit /b
)

echo Checking and installing requirements (psutil)...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Failed to install requirements. Please check internet connection.
    pause
    exit /b
)

echo.
echo Launching script...
python dual_sleeper.py
pause
