@echo off
chcp 65001 > nul
echo DISPLAY要求チェッカーを起動しています...
python "%~dp0test_display_request.py"
pause
