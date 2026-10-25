@echo off
chcp 65001 > nul
title بوت يوبينار - فحص الحالة والسجلات
cd /d "%~dp0"
python -m upinar_notifier status
echo.
pause
