@echo off
cd /d "%~dp0"
python local_daily.py --demo
if errorlevel 1 pause
