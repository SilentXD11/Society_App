@echo off
title Society App - phone link
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Download it from https://www.python.org/downloads/
  echo On the first install screen tick "Add python.exe to PATH", then run this again.
  pause
  exit /b
)

where ngrok >nul 2>nul
if errorlevel 1 goto noNgrok

cd backend
if not exist .venv (
  echo Setting up for the first time, this takes a minute...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python setup_env.py --phone
if errorlevel 1 ( pause & exit /b )

set DOMAIN=
for /f "tokens=1,* delims==" %%a in ('findstr /b "PHONE_DOMAIN=" .env') do set DOMAIN=%%b
if "%DOMAIN%"=="" ( echo Could not read PHONE_DOMAIN from backend\.env & pause & exit /b )

start "Society SERVER - keep this window open" cmd /k "set TRUST_PROXY=true&& .venv\Scripts\python -m uvicorn main:app --port 8000"
timeout /t 4 >nul
start "Society PHONE LINK - keep this window open" cmd /k "ngrok http 8000 --url=https://%DOMAIN%"
timeout /t 4 >nul
start "" https://%DOMAIN%/admin.html

echo.
echo ===============================================================
echo  On your phone open:   https://%DOMAIN%/
echo  Admin dashboard:      https://%DOMAIN%/admin.html
echo ===============================================================
echo  First time only: add these addresses in Auth0 (README, section "Use it on your phone").
echo  The link works while this laptop is on and both windows stay open.
echo.
pause
exit /b

:noNgrok
echo.
echo ngrok is not installed yet. It creates the secure link your phone opens. One-time setup:
echo.
echo  1. Sign up free:        https://dashboard.ngrok.com/signup
echo  2. Install it - open PowerShell and run:   winget install ngrok.ngrok
echo  3. Connect your account - copy the command shown at
echo        https://dashboard.ngrok.com/get-started/your-authtoken
echo     (it looks like:  ngrok config add-authtoken 2abc...)  and run it in PowerShell
echo  4. Claim your free domain: https://dashboard.ngrok.com/domains
echo.
echo  Then close PowerShell and double-click start-phone.bat again.
echo.
pause
