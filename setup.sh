#!/usr/bin/env bash
# One-shot setup: checks prerequisites, builds and starts the stack, installs the ramp
# orchestrator's virtualenv, and runs the smoke test. Safe to re-run.
# Pulls published images from Docker Hub, falling back to a local build (or use --build).
# Manual equivalent: see README "Quick start, option B".
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RUN_SMOKE=1
FORCE_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --no-smoke) RUN_SMOKE=0 ;;
    --build) FORCE_BUILD=1 ;;
    -h|--help) echo "Usage: ./setup.sh [--no-smoke] [--build]   (--build skips Docker Hub and builds images locally)"; exit 0 ;;
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

step "Getting images"
PUBLISHED="jmeter grafana graphql-target"
if [ "$FORCE_BUILD" = 0 ] && $DC pull $PUBLISHED; then
  echo "pulled published images from Docker Hub"
  $DC build jmeter-gui   # authoring container, built locally on top of the pulled jmeter image
else
  echo "building images locally (first run takes a few minutes)"
  $DC build jmeter       # jmeter-gui builds FROM this image, so it goes first
  $DC build
fi

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
  Run a 20 s test:   ./ramp.sh profiles/rest-minimal.yaml
  Web console:       ./console.sh   (then open http://localhost:8800)
  Grafana:           http://localhost:3004   (admin / admin)
MSG
