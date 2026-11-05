@echo off
title Society App launcher
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python is not installed.
  echo Download it from https://www.python.org/downloads/
  echo IMPORTANT: on the first install screen tick "Add python.exe to PATH".
  echo Then double-click this file again.
  pause
  exit /b
)

cd backend
if not exist .venv (
  echo Setting up for the first time, this takes a minute...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python setup_env.py

start "Society SERVER - keep this window open" cmd /k ".venv\Scripts\python -m uvicorn main:app --port 8000"
cd ..
start "Society PAGES - keep this window open" cmd /k "backend\.venv\Scripts\python -m http.server 5500"

timeout /t 4 >nul
start "" http://localhost:5500/admin.html
echo.
echo Admin dashboard: http://localhost:5500/admin.html
echo Resident app:    http://localhost:5500/
echo To stop everything, close the two black windows.
pause
