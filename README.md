# Boomi Load Testing Toolkit

Load testing for APIs published through Boomi, built on JMeter, Prometheus and Grafana in Docker Compose. You describe a test as a short YAML profile (or build one in the included HTML builder), and the toolkit generates the JMeter plan, runs it in containers, and reports per-endpoint latency, throughput and errors. Results land in a Grafana dashboard that keeps history, so runs can be compared over time. A saved baseline turns a performance regression into a non-zero exit code for CI.

Nothing in the engine is Boomi-specific. It was built to test APIs fronted by Boomi API Management (API key or basic auth), but it works against any HTTP API: REST, GraphQL or SOAP.

## What it does

- **Profile-driven runs.** A YAML profile defines the target, the requests, and the steps. `ramp.py` generates the JMeter plan from it, so test authors don't need to open the JMeter GUI.
- **Ramp, spike and phased load.** One schema covers all of them. Each step sets its own threads and instance count, and optionally its own duration.
- **TPS or request-rate targets.** A step can ask for `tps: 40` (flow iterations per second) or `rps: 80` (requests per second). The toolkit converts between them and prints both at launch.
- **Database load tests.** The same profiles can drive SQL queries over JDBC (PostgreSQL today), with a bundled sample database to try it on. Writes are off unless a profile opts in.
- **Chained requests.** A request can extract a value (for example an `id` from a list response) and use it in later paths.
- **Stop rules.** A run halts when the error rate or average response time crosses a threshold.
- **Regression gate.** Save a known-good run as a baseline, and later runs are compared against it. Exit code `4` means a regression.
- **Per-step summary report.** Samples, average, median, 90/95/99th percentiles, error rate and throughput per endpoint and step, printed at the end of the run.
- **Grafana dashboard.** 16 panels: KPI cards, percentiles, a connect-time vs latency vs response-time breakdown, an endpoint summary table, slowest endpoints, and errors by status code. Filter by `run_id` and `step_id`.
- **Slow-request detection** from the raw results files, with a configurable threshold.
- **Web console.** Run, stop and watch tests from a browser, with history and results, so nobody has to type shell commands.
- **Visual authoring.** Edit `.jmx` files in a local JMeter or in a browser (noVNC container), and build profiles in `ramp-profile-builder.html` or in the console's YAML editor.

## Architecture

```
ramp.py / run-test.sh
        |  generates plan, launches containers
        v
  jmeter (headless)  ---- metrics on :9270 ---->  Prometheus  ---->  Grafana
  jmeter-ramp (N instances per step, file-based discovery)             dashboards
        |
        v
  target API (Boomi Atom / gateway, or the bundled sample services)
```

| Service | Purpose | Local port |
|---|---|---|
| `jmeter`, `jmeter-ramp` | Headless JMeter 5.6.3 with `jmeter-prometheus-plugin`, started per run | none |
| `jmeter-gui` | Browser-based JMeter for authoring only, never for real load | 6080 |
| `prometheus` | Scrapes every JMeter instance | 9090 |
| `grafana` | Dashboard and datasource pre-provisioned | 3004 (`admin`/`admin`) |
| `rest-target`, `graphql-target` | Bundled sample APIs for trying the stack | 8010, 8011 |

All ports bind to `127.0.0.1`. SOAP samples point at a public calculator service.

## Quick start

You need Docker with the `docker-compose` command, and Python 3.9 or newer for the ramp orchestrator. Pick one of two ways to set up.

### Option A: setup script

```bash
./setup.sh                                 # --no-smoke skips the smoke test, --build forces a local image build
```

It checks prerequisites, creates `.env`, pulls the published images (`pythonicshahdev/boomi-loadtest-jmeter`, `-grafana`, `-graphql-target`) or builds them locally if the pull fails, starts Prometheus, Grafana and the sample targets, creates the `.venv-ramp` virtualenv, and runs the smoke test. It is safe to re-run.

### Option B: manual steps

```bash
cp .env.example .env                       # only needed for real, authenticated targets
docker-compose pull jmeter grafana graphql-target   # or: docker-compose build, to build locally
docker-compose build jmeter-gui
docker-compose up -d prometheus grafana rest-target graphql-target
./smoke-test.sh                            # confirms metrics reach Prometheus, not just that services respond

python3 -m venv .venv-ramp && source .venv-ramp/bin/activate
pip install -r requirements-ramp.txt
```

### Run your first test (either option)

```bash
./ramp.sh profiles/rest-minimal.yaml  # 20 s run against the bundled REST target
```

Open Grafana at http://localhost:3004 (`admin`/`admin`) and pick the printed `run_id`. Or start the web console with `./console.sh` and run it from the browser.

## Claude Code skill

`skills/boomi-load-testing/` is a Claude Code skill that walks through setup, profiles, safe runs and the silent-failure pitfalls. To use it, link or copy it into your skills folder:

```bash
mkdir -p ~/.claude/skills && ln -s "$PWD/skills/boomi-load-testing" ~/.claude/skills/boomi-load-testing
```

## Web console

You don't have to use the command line to run tests. Start the console once, then work in the browser:

```bash
./console.sh                 # then open http://localhost:8800
```

Pick a profile, see what it will do (phases, duration, peak rate), then Validate, Edit, Run, or Run and save as baseline. A live run shows progress, the current phase and the log. When it finishes you get the results table, the baseline comparison in plain words, and a link to that run in Grafana. Past runs are listed under History. It runs the same `ramp.py` underneath, so results are identical to the command line, and `./ramp.sh` keeps working for CI.

It listens on `127.0.0.1` only. To use it on a VM, see [Running the console on a VM](docs/usage-guide.md#running-the-console-on-a-vm-ec2-or-similar).

## Writing a profile

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 40
warmup_sec: 8                   # excluded from reported metrics
think_time_ms: 200
stop_rule:
  max_error_rate_pct: 5
  max_avg_response_ms: 3000
regression:
  baseline: boomi-phased-tps
  max_p95_increase_pct: 30
  min_p95_delta_ms: 25
target:
  protocol: http
  host: host.docker.internal    # the host machine, as seen from inside the container
  port: 8078
  auth: api_key                 # none | basic | api_key
requests:
  - name: List customers
    method: GET
    path: /ws/rest/proxy/v1/customers
    extract: id=$[0].customer_id
  - name: Get customer
    method: GET
    path: /ws/rest/proxy/v1/customers/${id}
steps:
  - { instances: 1, threads: 8,  tps: 10 }
  - { instances: 1, threads: 26, tps: 40 }
  - { instances: 1, threads: 50, tps: 80 }
```

```bash
./ramp.sh --validate profiles/rest-boomi-phased-tps.yaml    # check without launching anything
./ramp.sh --save-baseline profiles/rest-boomi-phased-tps.yaml   # record a known-good run
./ramp.sh profiles/rest-boomi-phased-tps.yaml               # later runs are compared to it
```

Example profiles are in `profiles/`, including a Boomi gateway template (`rest-boomi-gateway.example.yaml`), phased TPS runs, a spike pattern and slow-request detection.

`rps` and `tps` are caps, not guarantees. If the thread count and think time can't reach the target, the achieved rate lands below it. Add threads if the summary's `Thrpt` falls short.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean run |
| 2 | A stop rule triggered |
| 3 | At least one JMeter instance exited non-zero |
| 4 | Regression against the baseline |
| 5 | No usable data: a step recorded no measurable samples (too short, target unreachable, or every request failing) |

The summary report prints in every case. When requests failed, it also lists the most common error messages straight from JMeter's raw results, so a wrong password or a dead host shows up as text, not as a quiet zero.

## Testing a Boomi-published API

1. Copy `profiles/rest-boomi-gateway.example.yaml` and fill in the placeholders.
2. Put credentials in `.env` (`BOOMI_TEST_TARGET_API_KEY`, or the basic-auth pair). Test plans read them through `${__groovy(System.getenv("VAR"))}`. Do not use `${__env(...)}`: it is not a real JMeter function and silently resolves to literal text.
3. The host must be reachable from inside the JMeter container: a public or VPN hostname, or `host.docker.internal`. `localhost` will not work.
4. Start with the low settings in the example, and confirm limits with the owner before loading a shared runtime.

## Things worth knowing

- **Percentile resolution.** Percentiles come from Prometheus histogram buckets. The `ramp.jmx` plans use buckets from 1 ms to 10 s, so fast APIs resolve properly. The base `starter.jmx` and `smoke-test.jmx` plans now use the same buckets. Baselines saved before 2026-10-25 were recorded with coarse 50 ms-and-up buckets, so re-save those baselines.
- **Throughput cap.** Averages and p99 are trustworthy at any bucket setting. `rps` and `tps` can only throttle, never raise load.
- **Starter pause.** The `starter.jmx` and `smoke-test.jmx` plans include a 15 second pause so short demos survive a Prometheus scrape. Delete it when cloning a starter for a real test.
- **Transaction Controller.** Don't wrap samplers in a Transaction Controller with `parent=true`. With `jmeter-prometheus-plugin` 0.7.1 it hides the individual sampler labels from Prometheus.
- **Grafana changes.** After editing `docker/grafana/dashboards/` or `docker/grafana/provisioning/`, rebuild the image and also remove the `grafana-data` volume, or Grafana keeps the old dashboard. The steps are in `docs/operations.md`.
- **After a restart.** If Docker restarts, bring the long-running services back up before running `ramp.sh`, or the report shows "NO DATA".
- **Plugin GUI quirk.** Selecting the Prometheus Listener node in the JMeter GUI shows a dismissible error. It is cosmetic and does not affect runs.
- **Sample REST target.** `rest-target` pulls `pythonicshahdev/fastapi-commercial-source:latest` from Docker Hub.

## Repository layout

```
ramp.py, ramp.sh        orchestrator: profile -> plan -> runs -> report
console/, console.sh    web console (browser UI over ramp.py)
setup.sh                one-shot setup
run-test.sh             single-shot run of a starter plan
smoke-test.sh           end-to-end stack check
docker-compose.yml      the stack
profiles/               example and working YAML profiles
ramp-profile-builder.html   visual profile builder (open directly in a browser)
baselines/              saved regression baselines (commit these)
test-plans/             .jmx plans per protocol: starters and ramp variants
docker/                 image sources and config
  jmeter/ jmeter-gui/   JMeter images
  grafana/ prometheus/  provisioned dashboard, datasource and scrape config
  sample-services/      bundled sample targets
tests/                  unit tests for ramp.py and the console
docs/                   guides, design notes, Docker Hub overviews
skills/                 Claude Code skill for this toolkit
```

## Documentation

- [Usage guide](docs/usage-guide.md): step-by-step setup, running tests, profiles, Grafana.
- [Technical guide](docs/technical-guide.md): architecture, design decisions and the pitfalls found while building it.
- [Operations notes](docs/operations.md): authoring test plans, credentials, and operational gotchas.
- [Latency-by-hop page](docs/examples/boomi-latency-by-hop.html): example results from a Boomi API run.
- [Design notes](docs/design/): the original specs and implementation plans.

## Tests

```bash
.venv-ramp/bin/pip install -r requirements-dev.txt
.venv-ramp/bin/python -m pytest tests -q
```
