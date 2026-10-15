@echo off
REM Local daily run (use Windows Task Scheduler -> "Run whether user is logged on or not", once a day).
cd /d "%~dp0"
if not exist logs mkdir logs
python run_daily.py >> logs\run_%date:~-4%-%date:~4,2%-%date:~7,2%.log 2>&1
