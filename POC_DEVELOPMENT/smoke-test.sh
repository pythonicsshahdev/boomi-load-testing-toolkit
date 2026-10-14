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

echo "=== 5. Metrics pipeline (direct scrape + Prometheus) ==="
SMOKE_RUN_ID="smoketest-$(date +%s)"
docker-compose run --rm --use-aliases jmeter -n -t /test-plans/_infra-check/empty.jmx -JRUN_ID="$SMOKE_RUN_ID" > /tmp/smoketest-jmeter.log 2>&1 &
JMETER_PID=$!
sleep 4
# Bypass Prometheus entirely: curl the plugin's metrics port directly, from a
# *different* container, so the 127.0.0.1-bind and --use-aliases bugs can't
# hide behind "it worked from inside the jmeter container itself."
docker-compose exec -T prometheus wget -qO- http://jmeter:9270/metrics > /tmp/smoketest-metrics.txt 2>&1 || true
grep -q "jmeter_threads" /tmp/smoketest-metrics.txt \
  && pass "jmeter metrics port directly reachable from another container" \
  || fail "jmeter metrics port unreachable — check prometheus.ip=0.0.0.0 in jmeter.properties.append and --use-aliases on the run command"
wait "$JMETER_PID" || true
sleep 6

# Confirm Prometheus itself actually scraped it. Use a range query, not an
# instant one: the jmeter container is already gone by now, and an instant
# "value right now" query goes empty the moment a pull-based target
# disappears, even though the historical scrape succeeded.
NOW=$(date +%s)
START=$((NOW - 120))
SCRAPED=$(curl -s --data-urlencode "query=jmeter_threads" \
  --data-urlencode "start=${START}" --data-urlencode "end=${NOW}" --data-urlencode "step=5" \
  http://localhost:9090/api/v1/query_range \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['data']['result']))" 2>/dev/null || echo 0)
if [ "$SCRAPED" -gt 0 ] 2>/dev/null; then
  pass "Prometheus actually scraped jmeter metrics ($SCRAPED series found)"
else
  fail "Prometheus never scraped jmeter metrics — check prometheus.yml's scrape target and that --use-aliases was used"
fi
rm -f /tmp/smoketest-jmeter.log /tmp/smoketest-metrics.txt

echo "=== Automated checks complete. Remaining checks require a real test run and manual inspection: ==="
echo "  1. ./run-test.sh rest/starter.jmx"
echo "  2. ./run-test.sh rest/starter.jmx       (run twice — note both printed RUN_IDs)"
echo "  3. Open http://localhost:3004, dashboard 'Boomi Performance Testing — JMeter Overview'"
echo "     - confirm the run_id dropdown lists BOTH run ids from step 2, not just one blended series"
echo "     - confirm Request Rate / p95 Response Time / Active Threads panels show non-empty data"
echo "  4. Edit a .jmx from the local JMeter GUI, confirm the change is visible from jmeter-gui's noVNC session (and vice versa)"
