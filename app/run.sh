#!/usr/bin/env sh
#
# Starts the API and the frontend together, for a quick look at the PDP.
#
# It assumes the setup steps in README.md are done:
#   * backend dependencies installed   (python -m venv .venv; pip install -r requirements-dev.txt)
#   * frontend dependencies installed  (pnpm install, inside app/frontend)
#
# The backend runs on app/backend/.venv when that virtualenv exists, otherwise on
# whatever `python` is on PATH.
#
# The frontend dev server proxies /api to http://127.0.0.1:8000, so both
# processes are needed for the page to work. Ctrl+C stops both.
#
# Two terminals are still the clearer way to develop; this script exists so a
# reviewer can start everything with one command.

set -eu

here=$(cd "$(dirname "$0")" && pwd)
api_port=${API_PORT:-8000}

python_bin="python"
for candidate in "$here/backend/.venv/bin/python" "$here/backend/.venv/Scripts/python.exe"; do
    if [ -x "$candidate" ]; then
        python_bin="$candidate"
        break
    fi
done

if ! command -v "$python_bin" >/dev/null 2>&1; then
    echo "python was not found. See README.md for the setup steps." >&2
    exit 1
fi
if ! command -v pnpm >/dev/null 2>&1; then
    echo "pnpm was not found on PATH. See README.md for the setup steps." >&2
    exit 1
fi
if [ ! -d "$here/frontend/node_modules" ]; then
    echo "warning: app/frontend/node_modules is missing - run 'pnpm install' there first." >&2
fi

echo "Starting the API on http://127.0.0.1:$api_port (using $python_bin) ..."
# Started from app/backend - the same way the test suite and CI start it, so the
# command here is one that is actually exercised.
cd "$here/backend"
"$python_bin" -m uvicorn app.main:app --port "$api_port" &
api_pid=$!

cleanup() {
    kill "$api_pid" 2>/dev/null || true
    wait "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting the frontend (Vite) - open http://localhost:5173 ..."
cd "$here/frontend"
pnpm dev
