#!/usr/bin/env bash
# Double-click on Mac (or run ./start-mac.command on Linux). Keep this window open while using the app.
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python is not installed. Download it from https://www.python.org/downloads/ and run this again."
  read -r -p "Press Enter to close"; exit 1
fi

cd backend
[ -d .venv ] || { echo "Setting up for the first time, this takes a minute..."; python3 -m venv .venv; }
. .venv/bin/activate
pip install -q -r requirements.txt
python setup_env.py

python -m uvicorn main:app --port 8000 &
SERVER=$!
cd ..
python -m http.server 5500 >/dev/null 2>&1 &
PAGES=$!
trap 'kill $SERVER $PAGES 2>/dev/null' EXIT

sleep 3
URL=http://localhost:5500/admin.html
(command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || true
echo
echo "Admin dashboard: http://localhost:5500/admin.html"
echo "Resident app:    http://localhost:5500/"
echo "Log in with the admin email you entered. Close this window to stop everything."
wait
