#!/usr/bin/env bash
set -Eeuo pipefail

port="${PORT:-8090}"

uv run --no-dev uvicorn trade_brain.app:app --host 0.0.0.0 --port "$port" &
api_pid=$!

uv run --no-dev python -m trade_brain.decision_worker &
decision_pid=$!

uv run --no-dev python -m trade_brain.paper_quote_worker &
quote_pid=$!

cleanup() {
  kill "$api_pid" "$decision_pid" "$quote_pid" 2>/dev/null || true
}

trap cleanup INT TERM EXIT
wait "$api_pid" "$decision_pid" "$quote_pid"
