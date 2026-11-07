#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <protocol>/<test-plan>.jmx" >&2
  echo "Example: $0 rest/starter.jmx" >&2
  exit 1
fi

TEST_PLAN="$1"
RUN_ID="$(date -u +%Y%m%d-%H%M%S)"

echo "Running ${TEST_PLAN} with RUN_ID=${RUN_ID}"

docker-compose run --rm --use-aliases jmeter \
  -n \
  -t "/test-plans/${TEST_PLAN}" \
  -l "/test-plans/results/${RUN_ID}.jtl" \
  -JRUN_ID="${RUN_ID}"

echo "Done. RUN_ID=${RUN_ID} — set Grafana's run_id variable to this value to see this run (http://localhost:3004)."
