#!/usr/bin/env bash
set -euo pipefail

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

echo "=== 1. Bring up long-running services ==="
docker-compose up -d prometheus grafana rest-target graphql-target jmeter-gui
sleep 5
pass "docker-compose up -d completed"

echo "=== 2. Target reachability ==="
curl -sf http://localhost:8010/ping > /dev/null && pass "rest-target /ping reachable" || fail "rest-target /ping unreachable"
curl -sf -X POST http://localhost:8011/graphql -H "Content-Type: application/json" \
  -d '{"query":"query { customers(limit: 1) { customerId } }"}' > /dev/null \
  && pass "graphql-target reachable" || fail "graphql-target unreachable"
curl -sf "http://www.dneonline.com/calculator.asmx?WSDL" > /dev/null \
  && pass "public SOAP demo reachable" || fail "public SOAP demo unreachable (external dependency, not this stack)"

echo "=== 3. Grafana + Prometheus reachability ==="
curl -sf http://localhost:9090/-/healthy > /dev/null && pass "prometheus healthy" || fail "prometheus not healthy"
curl -sf http://localhost:3004/api/health > /dev/null && pass "grafana healthy" || fail "grafana not healthy"

echo "=== 4. jmeter-gui noVNC reachability ==="
curl -sf http://localhost:6080/vnc.html > /dev/null && pass "jmeter-gui noVNC page reachable" || fail "jmeter-gui noVNC unreachable"

echo "=== Automated checks complete. Remaining checks require a real test run and manual inspection: ==="
echo "  1. ./run-test.sh rest/starter.jmx"
echo "  2. ./run-test.sh rest/starter.jmx       (run twice — note both printed RUN_IDs)"
echo "  3. Open http://localhost:3004, dashboard 'Boomi Performance Testing — JMeter Overview'"
echo "     - confirm the run_id dropdown lists BOTH run ids from step 2, not just one blended series"
echo "     - confirm Request Rate / p95 Response Time / Active Threads panels show non-empty data"
echo "  4. Edit a .jmx from the local JMeter GUI, confirm the change is visible from jmeter-gui's noVNC session (and vice versa)"
