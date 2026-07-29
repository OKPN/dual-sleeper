@echo off
chcp 65001 > NUL
title Dual Sleeper - GPU Process Realtime Monitor
python_embed\python.exe test_gpu_monitor.py
pause
