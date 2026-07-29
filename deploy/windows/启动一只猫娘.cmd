@echo off
chcp 65001 >nul
set "ROOT=%~dp0"
if not exist "%ROOT%pyproject.toml" set "ROOT=%~dp0\..\..\"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%deploy\windows\server-start.ps1"
echo.
pause
