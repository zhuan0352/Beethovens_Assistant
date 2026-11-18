#!/usr/bin/env bash
# Start Beethoven Assistant at http://localhost:8377
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

exec .venv/bin/python -m uvicorn backend.main:app --port "${PORT:-8377}" "$@"
