@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv missing. Run: powershell -ExecutionPolicy Bypass -File setup.ps1
    pause
    exit /b 1
)
".venv\Scripts\python.exe" video_director_agent\gui.py
if %ERRORLEVEL% NEQ 0 pause
