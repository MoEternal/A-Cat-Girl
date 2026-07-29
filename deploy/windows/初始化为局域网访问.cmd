@echo off
chcp 65001 >nul
cd /d "%~dp0\..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0server-setup.ps1" -BindHost 0.0.0.0
echo.
echo WARNING: Restrict TCP 8732 with Windows Firewall. Do not expose it to the public Internet.
pause
