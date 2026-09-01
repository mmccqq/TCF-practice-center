#!/usr/bin/env bash
# Run backend and frontend together; Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

[ -x backend/.venv/bin/uvicorn ] || { echo "run: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt"; exit 1; }
[ -d frontend/node_modules ]     || { echo "run: (cd frontend && npm install)"; exit 1; }

trap 'kill 0' EXIT
(cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev) &
wait


# Start
#   │
#   ▼
# Enable strict error handling
#   │
#   ▼
# Go to the script's directory
#   │
#   ▼
# Check Python/Uvicorn environment
#   │
#   ├── Missing → Print setup instructions → Exit
#   │
#   ▼
# Check frontend node_modules
#   │
#   ├── Missing → Print npm install instructions → Exit
#   │
#   ▼
# Register cleanup handler
#   │
#   ▼
# Start backend in background
#   │
#   ▼
# Start frontend in background
#   │
#   ▼
# Wait
#   │
#   ▼
# Ctrl-C / processes exit
#   │
#   ▼
# trap runs → kill background processes