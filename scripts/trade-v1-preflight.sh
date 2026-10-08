#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
failures=0

pass() {
  printf 'PASS  %s\n' "$1"
}

warn() {
  printf 'WARN  %s\n' "$1"
}

fail() {
  printf 'FAIL  %s\n' "$1"
  failures=$((failures + 1))
}

check_command() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    pass "${command_name} is available"
  else
    fail "${command_name} is not available"
  fi
}

printf 'Trade V1 preflight\n'
printf 'Project: %s\n\n' "$PROJECT_ROOT"

for command_name in python3 node npm supabase; do
  check_command "$command_name"
done

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    pass "Docker daemon is running"
  else
    fail "Docker CLI exists but the daemon is not running"
  fi
else
  fail "Docker CLI is not available"
fi

if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  pass ".env exists"
else
  warn ".env is missing, copy .env.example and fill server-side credentials"
fi

if [[ -f "${PROJECT_ROOT}/supabase/seed.sql" ]]; then
  pass "Supabase seed file exists"
else
  fail "Supabase seed file is missing"
fi

if [[ -f "${PROJECT_ROOT}/infra/freqtrade/config.example.json" ]]; then
  if python3 - "${PROJECT_ROOT}/infra/freqtrade/config.example.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as config_file:
    config = json.load(config_file)

assert config["dry_run"] is True
assert config["exchange"]["key"] == ""
assert config["exchange"]["secret"] == ""
assert config["api_server"]["listen_ip_address"] == "0.0.0.0"
assert config["force_entry_enable"] is False
PY
  then
    pass "Freqtrade configuration is paper-only and container-bound"
  else
    fail "Freqtrade configuration safety checks failed"
  fi
else
  fail "Freqtrade example configuration is missing"
fi

if [[ -f "${PROJECT_ROOT}/infra/freqtrade/user_data/strategies/TradeV1Foundation.py" ]]; then
  pass "Freqtrade foundation strategy exists"
else
  fail "Freqtrade foundation strategy is missing"
fi

if (( failures > 0 )); then
  printf '\nPreflight blocked by %d check(s).\n' "$failures"
  exit 1
fi

printf '\nPreflight passed. Start Supabase migrations before starting the workers.\n'
