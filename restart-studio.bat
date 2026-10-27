@echo off
cd /d "%~dp0"
if not exist studio_data mkdir studio_data
echo Restarting Reelwright on http://localhost:8020 ...
taskkill /f /t /fi "WINDOWTITLE eq Reelwright server*" >nul 2>&1
timeout /t 2 /nobreak >nul
start "Reelwright server" /min cmd /c ".venv\Scripts\python.exe -m uvicorn studio.app:app --host 0.0.0.0 --port 8020 > studio_data\server_log.txt 2>&1"
timeout /t 4 /nobreak >nul
exit
