@echo off
cd /d "%~dp0\.."
echo Running demo (console copy: studio_data\demo_console.txt)
".venv\Scripts\python.exe" -u tools\demo_run.py > studio_data\demo_console.txt 2>&1
type studio_data\demo_console.txt
pause
