@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv missing. Run: powershell -ExecutionPolicy Bypass -File setup.ps1
    pause
    exit /b 1
)
echo KupkaProd web UI: http://localhost:8010
".venv\Scripts\python.exe" -m uvicorn video_director_agent.web_app:app --host 127.0.0.1 --port 8010
pause
