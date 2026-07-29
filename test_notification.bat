@echo off
chcp 65001 > nul
title Dual Sleeper Notification Test

python_embed\python.exe test_notification.py

pause
