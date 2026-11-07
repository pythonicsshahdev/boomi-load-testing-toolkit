---
name: boomi-load-testing
description: Use when someone wants to load test, stress test, ramp/spike test, benchmark, or find the breaking point of an API (Boomi-published or any REST, GraphQL, SOAP or JDBC target) with the Boomi Load Testing Toolkit (JMeter + Prometheus + Grafana). Covers setting the toolkit up, writing and validating a YAML profile, running it safely, reading the report and Grafana, saving a regression baseline, and CI gating. Also use when a run reports NO DATA, zero samples, exit code 5, or missing Grafana panels.
---

# Boomi Load Testing Toolkit

The toolkit is this repo. Read `README.md` at the repo root first for current commands, ports and profile fields; it is the source of truth. This skill holds the workflow and the pitfalls that fail silently.

## Workflow

1. **Setup (once).** From the repo root: `./setup.sh` (checks prerequisites, builds or pulls images, starts Prometheus/Grafana/sample targets, creates `.venv-ramp`, runs the smoke test). Manual steps are in the README "Quick start, option B". Needs the `docker-compose` command and Python 3.9+.
2. **Credentials.** Put the target's key in `.env` (`BOOMI_TEST_TARGET_API_KEY`, or the `..._BASIC_AUTH_USER/PASS` pair; DB tests use `BOOMI_TEST_DB_USER/PASSWORD`). Never print or paste `.env` values; only check they are set.
3. **Write a profile** in `profiles/` (copy `rest-boomi-gateway.example.yaml` for a Boomi gateway). Target host must be reachable from inside the container: use `host.docker.internal` for something on the host, never `localhost`.
4. **Validate:** `./ramp.sh --validate <profile>`; launches nothing.
5. **Confirm limits before real load.** A test against a shared Atom, gateway or production system needs the owner's go-ahead. Start with the low steps from the example and increase.
6. **Run:** `./ramp.sh <profile>` or use the web console (`./console.sh`, http://localhost:8800). Read the printed summary, then Grafana (http://localhost:3004, `admin`/`admin`), filter by `run_id`/`step_id`.
7. **Baseline and gate:** `./ramp.sh --save-baseline <profile>` once on a known-good run; later runs compare against it. Commit `baselines/*.json`.

## Exit codes
0 clean, 2 stop rule triggered, 3 an instance exited non-zero, 4 regression vs baseline, 5 no usable data. The summary prints in every case. In CI, fail the job on non-zero.

## Pitfalls (these fail without an obvious error)
- **Credentials in plans** must use `${__groovy(System.getenv("VAR"))}`. `${__env(...)}` is not a real JMeter function and silently resolves to literal text.
- **One-off `docker-compose run jmeter`** needs `--use-aliases` or Prometheus never resolves the container and the test "passes" with zero metrics. `run-test.sh` and `ramp.py` already do this.
- **NO DATA / missing steps:** after a Docker restart, bring the long-lived services back: `docker-compose up -d prometheus grafana rest-target graphql-target`. Prometheus also drops a target within ~4 s of it disappearing, so the report queries slightly before step end by design.
- **`rps`/`tps` are caps, not guarantees.** If `Thrpt` falls short of target, add threads. A step needs enough duration beyond `warmup_sec` to record samples.
- **Don't use a Transaction Controller with `parent=true`:** with jmeter-prometheus-plugin 0.7.1 it hides per-sampler labels from Prometheus.
- **Starter plans** (`starter.jmx`, `smoke-test.jmx`) contain a 15 s pause for demos; delete it when cloning one for a real test. Profile-generated plans don't have it.
- **Editing Grafana dashboards or provisioning:** rebuilding the image is not enough. Also remove the `grafana-data` volume (steps in `docs/operations.md`).
- **Error Rate panel showing "No data"** on a clean run is correct: the failure counter only exists after a first failure.
- **Percentiles** come from histogram buckets (1 ms to 10 s in all plans now). Baselines saved before 2026-10-25 used coarse buckets; re-save them.
- **Exposure:** keep ports on `127.0.0.1`. The console refuses non-local binding without `CONSOLE_TOKEN`. Database profiles are read-only unless the profile sets `allow_writes: true`; confirm before enabling writes.

## Not in scope
Mashery/Cloud API Management and MCP Gateway targets, Kubernetes/Helm and Boomi-side runtime metrics are not built. Say so rather than improvising.
