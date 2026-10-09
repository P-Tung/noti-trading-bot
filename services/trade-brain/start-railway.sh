#!/usr/bin/env bash
set -Eeuo pipefail

port="${PORT:-8090}"
python_bin="/app/.venv/bin/python"
uvicorn_bin="/app/.venv/bin/uvicorn"

echo "Decision worker environment: anthropic_key=$([[ -n "${ANTHROPIC_API_KEY:-}" ]] && echo configured || echo missing), claude_model=$([[ -n "${CLAUDE_MODEL:-}" ]] && echo configured || echo missing), telegram_token=$([[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] && echo configured || echo missing), telegram_chat_ids=$([[ -n "${TELEGRAM_CHAT_IDS:-${TELEGRAM_CHAT_ID:-}}" ]] && echo configured || echo missing)"

"$uvicorn_bin" trade_brain.app:app --host 0.0.0.0 --port "$port" &
api_pid=$!

decision_pid=""
if [[ -n "${ANTHROPIC_API_KEY:-}" && -n "${CLAUDE_MODEL:-}" ]]; then
  (
    while true; do
      "$python_bin" -m trade_brain.decision_worker || echo "Decision worker exited; restarting in 2 seconds"
      sleep 2
    done
  ) &
  decision_pid=$!
  echo "Decision worker launched with pid $decision_pid"
else
  echo "Decision worker disabled: ANTHROPIC_API_KEY and CLAUDE_MODEL are required."
fi

"$python_bin" -m trade_brain.paper_quote_worker &
quote_pid=$!

cleanup() {
  kill "$api_pid" "$quote_pid" 2>/dev/null || true
  if [[ -n "$decision_pid" ]]; then
    kill "$decision_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM EXIT
wait "$api_pid"
