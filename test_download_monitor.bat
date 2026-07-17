@echo off
chcp 65001 > nul
echo ダウンロードフォルダ監視テストを起動しています...
python "%~dp0test_download_monitor.py"
pause
