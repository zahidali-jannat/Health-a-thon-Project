#!/usr/bin/env bash
# Starts the backend (port 8000) and frontend (port 5173). Ctrl+C stops both.
set -e
cd "$(dirname "$0")"
[ -d "$HOME/.local/node/bin" ] && export PATH="$HOME/.local/node/bin:$PATH"

if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
[ -d frontend/node_modules ] || (cd frontend && npm install)

# Your data is kept between runs. The schema is updated automatically at start-up.
# Start completely fresh: (cd backend && .venv/bin/python -m app.reset)
(cd backend && .venv/bin/uvicorn app.main:app --port 8000) &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT

echo
echo "  Care team dashboard: http://localhost:5173/care-team"
echo "  Patient portal:      http://localhost:5173/patient"
echo
cd frontend && npx vite --port 5173
