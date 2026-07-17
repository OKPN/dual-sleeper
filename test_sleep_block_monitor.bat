@echo off
chcp 65001 > nul
echo スリープ禁止信号チェッカーを起動しています...
python "%~dp0test_sleep_block_monitor.py"
pause
