@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv missing. Run setup.bat first.
    pause
    exit /b 1
)
echo Reelwright: http://localhost:8020
start "" http://localhost:8020
".venv\Scripts\python.exe" -m uvicorn studio.app:app --host 0.0.0.0 --port 8020
pause
