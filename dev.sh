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
