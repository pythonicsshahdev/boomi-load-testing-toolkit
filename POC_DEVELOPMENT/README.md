# Boomi Performance Testing Stack

JMeter + Prometheus + Grafana load-testing stack for Boomi-published REST,
SOAP, and GraphQL APIs. See `docs/superpowers/specs/2026-10-12-jmeter-boomi-performance-stack-design.md`
for the full design.

## Authoring test plans

Two ways to build/edit `.jmx` files under `test-plans/`, same shared folder either way:

1. **Local JMeter GUI** — if you already have JMeter installed (e.g. via
   `brew install jmeter`), just open files directly from `test-plans/`.
   Optional: install [JMeter Studio](https://github.com/QAInsights/jmeter-studio)
   for the same modernized theme the `jmeter-gui` container ships with —
   download the latest jar from its Releases page, copy it into
   `$JMETER_HOME/lib/ext/`, restart JMeter, then Options → Themes.
2. **Browser-based GUI** — run `docker compose up -d jmeter-gui`, then open
   `http://localhost:6080/vnc.html`. No local JMeter install needed.

## Running a test

```bash
./run-test.sh rest/starter.jmx
