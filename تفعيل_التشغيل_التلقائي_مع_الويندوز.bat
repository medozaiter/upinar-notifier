@echo off
chcp 65001 > nul
title بوت يوبينار - تفعيل التشغيل التلقائي مع الويندوز
cd /d "%~dp0"
python -m upinar_notifier autostart
echo.
pause
