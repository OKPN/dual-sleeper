@echo off
chcp 65001 > nul
echo ==================================================
echo  Dual Sleeper - Discord Webhook Test Utility
echo ==================================================
echo.

python dual_sleeper.py --test-webhook
echo.
pause
