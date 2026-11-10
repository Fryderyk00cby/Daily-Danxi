@echo off
cd /d "%~dp0"
if exist "%~dp0DanXiDaily.exe" (
    start "" "%~dp0DanXiDaily.exe"
) else (
    echo Please run setup.cmd first to build DanXiDaily.exe.
    pause
)
