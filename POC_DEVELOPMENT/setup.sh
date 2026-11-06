#!/usr/bin/env bash
# One-shot setup: checks prerequisites, builds and starts the stack, installs the ramp
# orchestrator's virtualenv, and runs the smoke test. Safe to re-run.
# Manual equivalent: see README "Quick start, option B".
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUN_SMOKE=1
for arg in "$@"; do
  case "$arg" in
    --no-smoke) RUN_SMOKE=0 ;;
    -h|--help) echo "Usage: ./setup.sh [--no-smoke]"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 1 ;;
  esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

step "Checking prerequisites"
command -v docker >/dev/null || fail "docker is not installed. Install Docker Desktop (or Docker Engine) first."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start it and re-run."
command -v docker-compose >/dev/null || fail "'docker-compose' not found. This project's scripts call it directly (Docker Desktop includes it, or install the standalone binary)."
DC="docker-compose"
command -v python3 >/dev/null || fail "python3 is not installed (3.9 or newer is needed)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || fail "python3 is older than 3.9."
echo "docker ok, compose: $DC, python $(python3 -c 'import platform; print(platform.python_version())')"

step "Creating .env if missing"
if [ -f .env ]; then
  echo ".env already exists, leaving it alone"
else
  cp .env.example .env
  echo "created .env from .env.example (fill in credentials only if you test an authenticated API)"
fi

step "Building images (first run takes a few minutes)"
$DC build

step "Starting Prometheus, Grafana and the sample targets"
$DC up -d prometheus grafana rest-target graphql-target

step "Installing the ramp orchestrator (.venv-ramp)"
[ -d .venv-ramp ] || python3 -m venv .venv-ramp
.venv-ramp/bin/pip install --quiet --upgrade pip
.venv-ramp/bin/pip install --quiet -r requirements-ramp.txt

if [ "$RUN_SMOKE" = 1 ]; then
  step "Running the smoke test"
  ./smoke-test.sh
fi

cat <<MSG

Setup complete.
  Run a 20 s test:   ./ramp.sh ramp-profiles/rest-minimal.yaml
  Web console:       ./console.sh   (then open http://localhost:8800)
  Grafana:           http://localhost:3004   (admin / admin)
MSG
