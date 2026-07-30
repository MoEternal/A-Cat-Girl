@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\windows\update-package.ps1"
set "update_exit=%errorlevel%"
if not "%update_exit%"=="0" pause
exit /b %update_exit%
