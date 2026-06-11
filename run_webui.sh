#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8510}"
python3 -m uvicorn webui_server:app --host "$HOST" --port "$PORT"
