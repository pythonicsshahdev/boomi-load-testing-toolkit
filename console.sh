#!/usr/bin/env bash
# Starts the web console. Same virtualenv as ramp.sh. See README "Web console".
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ ! -x "$SCRIPT_DIR/.venv-ramp/bin/python3" ]; then
  echo "The Python environment is not set up yet. Run ./setup.sh first (or: python3 -m venv .venv-ramp && .venv-ramp/bin/pip install -r requirements-ramp.txt)." >&2
  exit 1
fi
# Pick up CONSOLE_TOKEN from .env (only that one variable) unless it is already set.
if [ -z "${CONSOLE_TOKEN:-}" ] && [ -f .env ]; then
  token_line="$(grep -E '^CONSOLE_TOKEN=' .env | tail -1 || true)"
  if [ -n "$token_line" ]; then export CONSOLE_TOKEN="${token_line#CONSOLE_TOKEN=}"; fi
fi
exec "$SCRIPT_DIR/.venv-ramp/bin/python3" -m console "$@"
