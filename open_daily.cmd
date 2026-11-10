@echo off
cd /d "%~dp0"
if exist "%~dp0DanXiDaily.exe" (
    start "" "%~dp0DanXiDaily.exe"
) else (
    python local_web.py
    if errorlevel 1 pause
)
