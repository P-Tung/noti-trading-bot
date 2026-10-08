#!/usr/bin/env bash
set -Eeuo pipefail

port="${PORT:-8090}"

uv run --no-dev uvicorn trade_brain.app:app --host 0.0.0.0 --port "$port" &
api_pid=$!

decision_pid=""
if [[ -n "${ANTHROPIC_API_KEY:-}" && -n "${CLAUDE_MODEL:-}" ]]; then
  uv run --no-dev python -m trade_brain.decision_worker &
  decision_pid=$!
else
  echo "Decision worker disabled: ANTHROPIC_API_KEY and CLAUDE_MODEL are required."
fi

uv run --no-dev python -m trade_brain.paper_quote_worker &
quote_pid=$!

cleanup() {
  kill "$api_pid" "$quote_pid" 2>/dev/null || true
  if [[ -n "$decision_pid" ]]; then
    kill "$decision_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT
wait "$api_pid"
