@echo off
chcp 65001 > nul
title بوت يوبينار - إلغاء التشغيل التلقائي
cd /d "%~dp0"
python -m upinar_notifier autostart --disable
echo.
pause
