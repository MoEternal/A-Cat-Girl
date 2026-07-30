@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
bash "%~dp0deploy\linux\update-package.sh"
set "update_exit=%errorlevel%"
if not "%update_exit%"=="0" pause
exit /b %update_exit%
