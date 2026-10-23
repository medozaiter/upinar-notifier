@echo off
chcp 65001 > nul
echo فحص اتصال بوت التيليجرام...
python "%~dp0test_telegram.py"
pause
