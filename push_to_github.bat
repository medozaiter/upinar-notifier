@echo off
chcp 65001 > nul
title Push UPINAR Notifier to GitHub

echo ========================================================
echo        رفع مشروع مراقب اليوبينار إلى GitHub
echo ========================================================
echo.
echo تنبيه أمان: ملفاتك الشخصية (config.json) محمية تماماً
echo ولن يتم رفعها بفضل ملف .gitignore.
echo.

set /p REPO_URL="الصق رابط مستودع GitHub الخاص بك (مثال: https://github.com/username/upinar-notifier.git): "

if "%REPO_URL%"=="" (
    echo [خطأ] لم يتم إدخال أي رابط.
    pause
    exit /b
)

git branch -M main
git remote remove origin 2>nul
git remote add origin %REPO_URL%
echo.
echo جاري الرفع إلى GitHub...
git push -u origin main

echo.
echo ========================================================
echo إذا طلب منك تسجيل الدخول، سجّل عبر المتصفح لتكتمل العملية.
echo ========================================================
pause
