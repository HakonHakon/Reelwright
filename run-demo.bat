@echo off
cd /d "%~dp0"
if not exist studio_data mkdir studio_data
echo Restarting Reelwright on http://localhost:8020 ...
taskkill /f /t /fi "WINDOWTITLE eq Reelwright server*" >nul 2>&1
timeout /t 2 /nobreak >nul
start "Reelwright server" /min cmd /c ".venv\Scripts\python.exe -m uvicorn studio.app:app --host 127.0.0.1 --port 8020 > studio_data\server_log.txt 2>&1"
timeout /t 6 /nobreak >nul
echo Running the Rain Boat demo (log: studio_data\demo_log.txt) ...
".venv\Scripts\python.exe" -u tools\demo_run.py > studio_data\demo_console.txt 2>&1
type studio_data\demo_console.txt
echo.
echo Finished. Studio stays running at http://localhost:8020
pause
