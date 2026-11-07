#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -x "$SCRIPT_DIR/.venv-ramp/bin/python3" ]; then
  echo "The Python environment is not set up yet. Run ./setup.sh first (or: python3 -m venv .venv-ramp && .venv-ramp/bin/pip install -r requirements-ramp.txt)." >&2
  exit 1
fi
exec "$SCRIPT_DIR/.venv-ramp/bin/python3" "$SCRIPT_DIR/ramp.py" "$@"
