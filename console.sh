#!/usr/bin/env bash
# Starts the web console. Same virtualenv as ramp.sh. See README "Web console".
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
# Pick up CONSOLE_TOKEN from .env (only that one variable) unless it is already set.
if [ -z "${CONSOLE_TOKEN:-}" ] && [ -f .env ]; then
  token_line="$(grep -E '^CONSOLE_TOKEN=' .env | tail -1 || true)"
  if [ -n "$token_line" ]; then export CONSOLE_TOKEN="${token_line#CONSOLE_TOKEN=}"; fi
fi
exec "$SCRIPT_DIR/.venv-ramp/bin/python3" -m console "$@"
