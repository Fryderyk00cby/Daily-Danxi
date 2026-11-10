@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
if errorlevel 1 (
    echo Installation failed. Please check the message above.
) else (
    echo Ready. Double-click DanXiDaily.exe to start.
)
pause
