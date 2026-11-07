# Boomi Load Testing Toolkit: Technical Source Document

**State described:** the repository at commit `0408f5a` (2026-11-07). Everything here describes what exists and was verified, plus a clearly marked list of what was not verified or not built.

**Purpose of this document.** It is a complete, self-contained source for writing a detailed technical guide. It assumes the reader has no access to the repository, the chat history or the people who built it. Every field, rule, number, endpoint, decision and pitfall that matters is written out in full. Where something is a judgement call or unverified, it says so.

**How to use it.** Section 0 lists what each section covers, so gaps are easy to spot. Sections 1 to 3 give the picture. Sections 4 to 14 are the reference. Sections 15 to 19 cover lessons, limits and operations. The appendices are lookup tables.

---

## 0. Coverage map

| # | Section | What it covers |
|---|---|---|
| 1 | Overview | What the toolkit is, who it is for, what it is not |
| 2 | History | How and when it was built, stage by stage |
| 3 | Architecture | Components, ports, data flow, network, images |
| 4 | Repository layout | Every folder and the important files |
| 5 | Decision log | Why each major choice was made, and what was rejected |
| 6 | The profile format | Every field, every validation rule, with examples |
| 7 | Authentication | All six auth types, environment variables, OAuth internals, Cloud API Management |
| 8 | Test plans and plan generation | Hand-built plans, generated plans, JDBC plans |
| 9 | Metrics model | Collectors, labels, buckets, Prometheus configuration |
| 10 | The orchestrator (`ramp.py`) | The full run flow, queries, stop rules, regression gate, exit codes |
| 11 | The web console | Architecture, security, API reference, every feature, design |
| 12 | Grafana | Dashboard, variables, every panel's query, provisioning |
| 13 | Docker images and publishing | The four custom images, tags, the publish procedure |
| 14 | Sample services | The bundled targets and what each serves |
| 15 | Setup and operations | Both setup paths, scripts, daily operation, restarts |
| 16 | Security model | Every protection and every known exposure |
| 17 | Testing and verification | The test suite, the live checks, what was and was not verified |
| 18 | Pitfalls and lessons | Every silent failure found, with cause and fix |
| 19 | Limitations and roadmap | What is not built, what is parked |
| A | Exit codes | Lookup |
| B | Environment variables | Lookup |
| C | Ports | Lookup |
| D | Metric reference | Lookup |
| E | Console API reference | Lookup |
| F | Profile examples shipped | Lookup |
| G | Glossary | Lookup |
| H | Command cheat sheet | Lookup |

---

## 1. Overview

### 1.1 What it is

A load-testing toolkit for APIs and databases, built on Apache JMeter, Prometheus and Grafana, packaged as Docker containers. A person describes a test as a short YAML file (a **profile**), or builds one in a browser form. The toolkit generates the JMeter test plan, runs it in containers, and reports per-endpoint latency, throughput and errors, both as a text report and in a Grafana dashboard that keeps history. A saved **baseline** turns a performance regression into a non-zero exit code, so it can gate a pipeline. A **web console** lets people run and watch tests without a command line.

It was built to test APIs published through Boomi (the Boomi Atom's API gateway and Boomi Cloud API Management), but nothing in the engine is Boomi-specific. It works against any HTTP API (REST, GraphQL, SOAP) and PostgreSQL databases.

### 1.2 Who it is for

The people expected to write and maintain tests are **low-code Boomi users, not software developers**. That one fact drove the engine choice (JMeter, with GUI authoring, over a code-first tool), the YAML profile format, the browser form builder and the web console.

### 1.3 What it does, as a feature list

- **Profile-driven runs.** A YAML profile defines the target, the requests and the phases. `ramp.py` generates the JMeter plan, so authors never open the JMeter GUI unless they want to.
- **Ramp, spike and phased load.** One schema covers all. Each phase (called a *step* in the file) sets its own thread count and number of parallel JMeter instances, and optionally its own duration.
- **TPS or request-rate targets.** A phase can target `tps` (whole flow iterations per second) or `rps` (individual requests per second). The toolkit converts and prints both.
- **Six authentication types:** none, basic, API key (header or query string), API key with per-request signature, bearer token, and OAuth 2.0 client credentials with automatic refresh.
- **Boomi Cloud API Management (Mashery) support** through the API-key and signature types.
- **Database load tests** over JDBC against PostgreSQL, read-only unless explicitly allowed.
- **Chained requests.** A request can extract a value from a response (a JSON path) and use it in later requests.
- **Stop rules.** A run halts when the error rate or average response time crosses a limit.
- **Regression gate.** Save a known-good run as a baseline; later runs are compared to it; exit code 4 means regression.
- **Per-phase, per-endpoint summary report** with samples, average, median, p90, p95, p99, error rate and throughput.
- **Slow-request detection** from JMeter's raw results files.
- **Grafana dashboard** with 16 panels, run and phase filters, and step annotations.
- **Web console** with a load-curve view, pre-run checklist, one-request test, live numbers, notes, history filters, run comparison, CSV export, HTML report, duplicate and delete of profiles, risk confirmation, optional Slack or Teams notification.
- **Form builder** page for creating profiles without writing YAML.
- **Claude Code skill** (`skills/boomi-load-testing/`) that guides setup, profiles and safe runs.
- **Published Docker images** so setup is a pull, not a build.

### 1.4 What it is not

- Not a distributed-cluster load platform. It runs on one machine (a laptop or a VM) with Docker. Kubernetes and Helm are parked (section 19).
- Not tied to Boomi. The sample profiles are Boomi-flavoured; the engine is generic.
- Not a replacement for Boomi-side monitoring. It measures what a client sees. It does not collect Boomi runtime metrics.
- Not a production service. The bundled sample targets and default credentials are for trying the toolkit.

---

## 2. History

The work happened over about four weeks, in stages. Dates are from the commit log (2026).

### Stage 0: Decision and design (Oct 12)
An earlier k6-based design existed. When it surfaced that test authors are low-code Boomi users, the engine was re-evaluated and **JMeter was chosen** (k6 parked, not deleted). A design spec and an 11-task implementation plan were written (`docs/design/`). Mashery support and MCP Gateway support were explicitly deferred. The toolkit was deliberately kept **lightweight and standalone**, as the counterpart to a separate, heavier performance project that integrates OpenTelemetry, OpenSearch and Boomi Data Hub.

### Stage 1: Base stack (Oct 13 to 14)
Docker Compose stack: headless JMeter image with the Prometheus plugin, browser-based JMeter authoring container (`jmeter-gui` with noVNC), Prometheus, Grafana image with a provisioned dashboard, a sample REST target, a new sample GraphQL target. Three starter plans (REST, GraphQL, SOAP) with a two-step correlated flow. `run-test.sh` and `smoke-test.sh`. A final review found one critical issue and eight important ones, all fixed (notably the non-existent `${__env()}` function; section 18).

### Stage 2: Distributed ramp and spike generator (Oct 14 to 15)
`ramp.py`: parallel multi-instance launch per step, Prometheus file-based service discovery, stop rules, JMeter-style summary report, slow-request detection, graceful interrupt handling, confirmation gate, CPU-saturation check, `--validate`, Grafana annotations. A browser form for building profiles. A second review found two critical issues (an unsummed multi-instance query that inverted results as load scaled; silent loss of a step with no data) and nine important ones, all fixed.

### Stage 3: Dashboard expansion (Oct 15)
Dashboard grew from 4 to 16 panels (KPI cards, percentiles, connect vs latency vs response, endpoint table, slowest endpoints, errors by code). TPS panel added, then made generic with a variable.

### Stage 4: Endpoint-driven profiles, pacing, regression gate, real Boomi runs (Oct 17 to 19)
Profiles gained `target:` and `requests:` blocks, so `ramp.py` generates the plan. Added think time, per-phase `rps`, then `tps`, and the baseline regression gate. First real runs against a Boomi API on a local Atom, phased 10/20/40/80/20/10, with TPS-based profiles and a latency-by-hop explainer page.

### Stage 5: Console and JDBC (Nov 2 to 4)
Fresh-clone install fixes. The 20-minute phased TPS profile. The web console. JDBC database load testing, and empty runs made to fail loudly (exit code 5).

### Stage 6: Publishing and hardening (Nov 6 to 7)
`setup.sh`; MIT license; three Docker images published under `boomi-loadtest-*`; Docker Hub overviews; the repository restructured (toolkit at the root, undated docs); OAuth 2.0 client credentials and bearer auth; console redesign and run tools; live throughput shown in TPS; Boomi Cloud API Management auth; roadmap. The GitHub repo was renamed to `boomi-load-testing-toolkit` and pushed to a public personal repo and a Boomi-internal repo.

### Evidence from real runs
Real runs against a customers REST API (list then get-by-id) through a local Boomi Atom (port 8078), with a FastAPI backend reachable directly for comparison:
- Phased 10/20/40/80/20/10 requests per second: 0 errors on both paths. The Boomi layer added about 4 ms at 80 req/s and 10 to 15 ms at low rates. One Boomi p99 spike (about 590 to 760 ms) in the first 20 req/s phase did not recur in later runs (likely cold start, unproven).
- Phased 10/20/40/80/20/10 **TPS** (peak 80 TPS = 160 req/s), 40-second phases: 0 errors, every phase hit its target (79.7 TPS at the 80 TPS peak; one phase 39.3 against 40). Boomi-layer overhead about 7 ms (list) and 6 ms (get) at 160 req/s. p95 about 48 to 50 ms.
- No breaking point was found. Limits above 80 TPS were not tried because they load the user's local Atom.
- Caveat at the time: percentiles p50 to p95 were bucket artifacts for fast APIs because histogram buckets started at 50 ms. Buckets were refined to start at 1 ms on 2026-10-25. Averages and p99 were trustworthy throughout.
- The Boomi-layer overhead figures come from differencing two runs (through Boomi versus direct), not from a real trace.

---

## 3. Architecture

### 3.1 Diagram

```
 YAML profile  ---->  ramp.py / ramp.sh   <----  web console (browser, port 8800)
 (or builder form)        |  generates a JMeter plan (test-plans/generated/<run>.jmx)
                          |  launches N JMeter containers per step (docker-compose run)
                          |  writes one Prometheus target file per container
                          v
   jmeter-ramp containers  (JMeter 5.6.3 + Prometheus listener, metrics on :9270)
        |  load                                     ^ scrape every 5 s (file-based discovery)
        v                                           |
   target API / database  <--- Boomi Atom, Cloud API Management, or bundled sample ---   Prometheus (9090)
                                                                                           |
                                              Grafana (3004) reads Prometheus  <-----------+
                                              ramp.py reads Prometheus for reports and stop rules
```

### 3.2 Components

| Component | What it is | Notes |
|---|---|---|
| `ramp.py` | Python orchestrator | Generates plans, launches containers, queries Prometheus, prints reports, applies stop rules and the regression gate. Also does the OAuth pre-check and the one-request test. |
| `console/` | Python web console | Standard-library HTTP server, one HTML page, runs `ramp.py` as a subprocess. Port 8800. A host process, not a container. |
| `jmeter` / `jmeter-ramp` | The load generator | Same image, two service names (section 5, decision 7). Started per run, not left running. |
| `jmeter-gui` | Authoring only | JMeter GUI in a browser through noVNC. Never used to generate real load. |
| `prometheus` | Metrics store | Scrapes every 5 s. Two jobs: a static one and a file-discovery one. |
| `grafana` | Dashboards | Dashboard and datasource are baked into the image. |
| `rest-target`, `graphql-target`, `oauth-target`, `postgres-target` | Bundled sample targets | So the whole stack can be tried with no real system. |
| Builder page | `ramp-profile-builder.html` | Standalone HTML form that produces a profile. |
| Skill | `skills/boomi-load-testing/` | Claude Code guidance. |

### 3.3 Data flow for one run

1. `ramp.py` reads and validates the profile. If it has `target` and `requests`, it generates a JMeter plan from the profile and a template plan.
2. For OAuth profiles it fetches one token from inside a container as a pre-check.
3. For each step it launches the requested number of `jmeter-ramp` containers in parallel, each with `-JTHREADS`, `-JDURATION` and, if paced, `-JRPS_PER_MIN`. For each container it writes a Prometheus target file declaring `run_id` and `step_id` labels.
4. Each container exposes metrics on port 9270. Prometheus notices the target file within 5 s and scrapes every 5 s.
5. Each container writes a raw results file (`.jtl`).
6. After each step `ramp.py` queries Prometheus (at a time slightly before the step ended) to evaluate stop rules. After all steps it queries again for the summary report, scans the `.jtl` files for failures and slow requests, applies the regression gate, and exits with a code.
7. Grafana shows the same data live, with step start and end annotations posted by `ramp.py`.

### 3.4 Network

All services share one Docker bridge network, `perf-test-net`. JMeter containers reach sample targets by service name (for example `oauth-target:8000`). To reach something on the host machine they use `host.docker.internal` (Docker Desktop provides it; on Linux the compose file maps it with `host-gateway`). They can never use `localhost`, which is the container itself.

### 3.5 Ports

See Appendix C. Every published port binds to `127.0.0.1` only.

### 3.6 Compose project name

`docker-compose.yml` sets `name: boomi-loadtest`, so containers are `boomi-loadtest-<service>-1`, the network is `boomi-loadtest_perf-test-net` and the volumes are `boomi-loadtest_prometheus-data` and `boomi-loadtest_grafana-data`. (Before the restructure the project name came from the folder and was `poc_development`.)

---

## 4. Repository layout

```
ramp.py, ramp.sh        orchestrator (ramp.sh runs ramp.py from the .venv-ramp virtualenv)
console/, console.sh    web console: server.py, runs.py, profiles.py, extras.py, static/
setup.sh                one-shot setup
run-test.sh             single-shot run of a starter plan
smoke-test.sh           end-to-end stack check (9 checks)
docker-compose.yml      the stack
.env.example            template for credentials (.env is gitignored)
profiles/               20 example and working YAML profiles (Appendix F)
ramp-profile-builder.html   standalone form builder
baselines/              saved regression baselines, JSON (committed on purpose)
test-plans/             JMeter plans: rest/, graphql/, soap/ (starter + ramp), _infra-check/; generated/ and results/ are gitignored
docker/
  jmeter/               Dockerfile (JMeter + plugin + Postgres driver) and jmeter.properties.append
  jmeter-gui/           Dockerfile (noVNC authoring) and supervisord.conf
  grafana/              Dockerfile, dashboards/jmeter-performance.json, provisioning/
  prometheus/           prometheus.yml and targets/ (generated target files)
  sample-services/      graphql-strawberry/, oauth-stub/, postgres/init.sql
tests/                  test_ramp.py, test_console.py
docs/                   this guide, usage-guide.md, operations.md, roadmap.md, design/, dockerhub/, examples/
skills/boomi-load-testing/SKILL.md
LICENSE                 MIT
requirements-ramp.txt   PyYAML
requirements-dev.txt    pytest
```

Files kept locally but not tracked by git (see `.gitignore`): `.env`, `.local/`, `.claude/`, `CLAUDE.md`, `active-development/` (Boomi workspace scaffolding from a template, unrelated to the toolkit), `console/runs/` (run history), `test-plans/generated/`, `test-plans/results/*.jtl`, `jmeter.log`, `.venv-ramp/`.

---

## 5. Decision log

Each entry: the choice, the reason, and what was rejected.

1. **JMeter, not k6.** Test authors are low-code. JMeter gives GUI authoring, built-in extractors, CSV-driven data and a path to distributed load. k6 is a smaller container with native Prometheus output but every scenario, SOAP, GraphQL and correlation, is hand-written JavaScript. k6 was parked, not deleted.
2. **Prometheus, not InfluxDB.** Pull-based scraping and `ServiceMonitor` discovery fit Kubernetes, which was an eventual target. InfluxDB would need JMeter's Backend Listener.
3. **`jmeter-prometheus-plugin` as a dedicated Listener.** It is not a generic Backend Listener. The design spec had this wrong at first and was corrected.
4. **Python orchestrator, not bash.** YAML, JSON, CSV and signal handling are exactly what is fragile in shell. `ramp.sh` is a thin wrapper.
5. **Profiles generate the JMeter plan.** Authors write a short YAML file instead of editing XML. The profile's `test_plan` acts as a template: its thread group and Prometheus listener are kept, everything between is generated. A profile without `target`/`requests` runs its hand-built plan unchanged.
6. **Prometheus file-based service discovery for parallel instances.** A static scrape target cannot represent a varying number of containers per step. Each container gets its own target file with `run_id` and `step_id` labels; Prometheus picks files up within 5 s with no reload.
7. **A separate `jmeter-ramp` service.** If ramp containers ran under the `jmeter` service, `--use-aliases` would register the alias `jmeter` for them, which collides with the base project's static `jmeter:9270` target and corrupts that job's counters. A separate service name keeps the two scrape jobs apart.
8. **TPS is the unit.** Enterprises size load in transactions per second, meaning whole flow iterations. `tps` is converted to requests per second as `tps x number of requests`, which is exact because every request in the flow runs once per iteration. The console and report lead with TPS; a profile written in `rps` keeps requests per second as the headline.
9. **Reports query Prometheus, not just the results files.** Percentiles come from histograms. The results files are used only for failures and slow requests, because they do not depend on scrape timing.
10. **Percentiles from histogram buckets.** Prometheus cannot expose true min or max, only bucket boundaries, so the report omits Min and Max.
11. **Throughput panel proxy for TPS.** A Transaction Controller with `parent=true` hides per-sampler labels from this plugin (section 18), so TPS is derived from the rate of the flow's first request, chosen with a dashboard variable.
12. **Secrets never in profiles.** A profile names the `.env` variable that holds a secret. Variable names are validated against `^[A-Z][A-Z0-9_]*$` so a pasted secret value is rejected with a clear message.
13. **OAuth token handling lives inside JMeter, as a pre-processor.** It is not a sampler, so token requests never appear in load-test metrics. A pre-flight check from `ramp.py` stops a bad configuration before load starts.
14. **JDBC read-only by default.** A write query needs `allow_writes: true` in the profile.
15. **The console is a standard-library server with one HTML page.** No build step, no framework, no external requests. It runs the same `ramp.py`, so results are identical to the command line.
16. **Loopback-only by default.** The console and every published port bind to `127.0.0.1`. Binding elsewhere requires a token of at least 16 characters.
17. **Publish only images with real baked-in value.** Three images are published (JMeter with plugin and driver, Grafana with dashboard, the GraphQL sample). `jmeter-gui` is built locally from the JMeter image. `oauth-target` is built locally.
18. **MIT license**, copyright 2026 Shah Abdullah. JMeter, the Prometheus plugin, Prometheus and Grafana are not bundled as code and carry their own licenses.
19. **Repository restructured and renamed** to look like a product, not a proof of concept: toolkit at the root, `docker/` for image sources, undated doc names, repo named `boomi-load-testing-toolkit`.
20. **Kubernetes and Helm parked.** The runner is Docker-specific, so a chart needs a second runner, not just templates (section 19).
21. **Self-hosted fonts in the console** so it looks the same offline and behind corporate proxies.
22. **The query-string key is kept out of results files** by turning off JMeter's URL column, so credentials do not land in `.jtl` files.

---

## 6. The profile format

A profile is a YAML file in `profiles/`. This section is the complete reference. The validator is `validate_profile()` in `ramp.py`; `./ramp.sh --validate <file>` runs it without launching anything. The console runs the same validator.

### 6.1 Top-level keys

| Key | Required | Type | Meaning and rules |
|---|---|---|---|
| `test_plan` | yes | string | Path under `test-plans/`, for example `rest/ramp.jmx`. With `target`/`requests` it is the template; otherwise it runs as written. |
| `steps` | yes | list, non-empty | The phases. See 6.2. |
| `step_duration_sec` | conditional | number >= 1 | Default duration for steps without their own `duration_sec`. Required unless every step sets `duration_sec`. |
| `warmup_sec` | no | number >= 0 | Seconds at the start of each step excluded from reported metrics. Default 0. |
| `think_time_ms` | no | number >= 0 | Pause after each request. Only with `target`/`requests`. |
| `slow_request_threshold_ms` | no | number > 0 | Enables slow-request detection above this latency. |
| `stop_rule` | no | mapping | `max_error_rate_pct` (0 to 100) and/or `max_avg_response_ms` (> 0). |
| `regression` | no | mapping | `baseline` (name: letters, digits, `.`, `_`, `-`), optional `max_p95_increase_pct`, `max_error_rate_increase_pct`, `min_p95_delta_ms` (each >= 0). |
| `target` | with `requests` | mapping | Where to send load. See 6.3. |
| `requests` | with `target` | list, non-empty | What to send. See 6.4. |

`target` and `requests` must appear together or not at all.

### 6.2 Steps

Each step is a mapping:

| Key | Required | Rule |
|---|---|---|
| `instances` | yes | positive integer. Number of JMeter containers run in parallel. |
| `threads` | yes | positive integer. Threads per instance. |
| `duration_sec` | no | number >= 1. Overrides `step_duration_sec`. |
| `tps` | no | number > 0. Target flow iterations per second for the whole step. Needs `target`/`requests`. |
| `rps` | no | number > 0. Target requests per second for the whole step. Needs `target`/`requests`. |

`tps` and `rps` together in one step is an error. Total load is `instances x threads`. `tps` and `rps` are caps, not guarantees: if the thread count and think time cannot reach the target the achieved rate lands below it. The fix is more threads. A step without `tps` or `rps` is unthrottled.

### 6.3 Target (HTTP)

| Key | Rule |
|---|---|
| `protocol` | `http` (default) or `https`. |
| `host` | required. Give a bare hostname (the form builder warns on a scheme or path). |
| `port` | integer 1 to 65535, optional. |
| `auth` | one of `none`, `basic`, `api_key`, `api_key_signature`, `bearer`, `oauth2_client_credentials`. Default `none`. Details in section 7. |

The auth type adds its own keys (section 7).

### 6.4 Requests (HTTP)

| Key | Rule |
|---|---|
| `name` | required, unique within the profile. It becomes the sampler name and the Grafana label. |
| `method` | `GET` (default), `POST`, `PUT`, `PATCH`, `DELETE`. |
| `path` | required, must start with `/`. May contain `${var}` from an earlier extract. |
| `body` | optional string. Sent raw; adds a JSON `Content-Type` header. |
| `extract` | optional `var=$.json.path`. Reads a value from this response for later requests, for example `id=$[0].customer_id`. |
| `think_time_ms` | optional, overrides the profile-level pause for this request. |

Every request runs once per iteration, in order.

### 6.5 Database targets (JDBC)

`target.protocol: jdbc`.

| Key | Rule |
|---|---|
| `driver` | currently only `postgresql`. |
| `host`, `database` | required. |
| `port` | optional, default 5432. |
| `user_env`, `password_env` | optional names of `.env` variables (defaults `BOOMI_TEST_DB_USER`, `BOOMI_TEST_DB_PASSWORD`). |
| `allow_writes` | boolean, default false. |
| `auth` | not allowed for JDBC. |

Each request is a query: `name` (unique), `sql` (required), `query_type` (`select` default, or `update`), `params` (list of `{value, type}`, with `type` one of INTEGER, BIGINT, SMALLINT, VARCHAR, DECIMAL, DOUBLE, BOOLEAN, TIMESTAMP). The number of `?` placeholders must equal the number of params. A param value cannot contain a comma unless it uses `${...}`. HTTP fields (`method`, `path`, `body`, `extract`) are rejected. An `update` query is rejected unless `allow_writes: true`.

### 6.6 How TPS and RPS convert

`requests_per_second = tps x number_of_requests_in_the_flow`. Example: 80 TPS with 2 requests per iteration is 160 req/s. Each instance gets its share: `RPS_PER_MIN = requests_per_second x 60 / instances`, passed as `-JRPS_PER_MIN`. The generated plan has a JMeter Constant Throughput Timer (calculation mode 2, shared across the active threads of the thread group) reading `${__P(RPS_PER_MIN,6000000)}`. The default 6,000,000 per minute means effectively unthrottled.

### 6.7 Minimal and fuller examples

Minimal (runs the bundled sample for about 20 s):
```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 20
target: { protocol: http, host: rest-target, port: 4005, auth: none }
requests:
  - { name: List customers, method: GET, path: "/customers?limit=5", extract: "id=$[0].customer_id" }
  - { name: Get customer,   method: GET, path: "/customers/${id}" }
steps:
  - { instances: 1, threads: 3 }
```

Phased TPS against a Boomi gateway with a stop rule and regression gate:
```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 40
warmup_sec: 8
think_time_ms: 200
stop_rule: { max_error_rate_pct: 5, max_avg_response_ms: 3000 }
regression: { baseline: boomi-phased-tps, max_p95_increase_pct: 30, min_p95_delta_ms: 25 }
target: { protocol: http, host: host.docker.internal, port: 8078, auth: api_key }
requests:
  - { name: List customers, method: GET, path: /ws/rest/proxy/v1/customers, extract: "id=$[0].customer_id" }
  - { name: Get customer,   method: GET, path: "/ws/rest/proxy/v1/customers/${id}" }
steps:
  - { instances: 1, threads: 8,  tps: 10 }
  - { instances: 1, threads: 26, tps: 40 }
  - { instances: 1, threads: 50, tps: 80 }
```

### 6.8 Ramp versus spike

Both are the same mechanism. A ramp is a profile whose steps increase monotonically; a spike goes up, down and up irregularly. There is no separate mode.

---

## 7. Authentication

`target.auth` selects the scheme. Secrets are never written in a profile: the profile names the `.env` variable, and the JMeter container reads it from its environment (`${__groovy(System.getenv("NAME"))}`; see the `__env` pitfall in section 18).

| `auth` | What is sent | Extra profile keys | `.env` variables |
|---|---|---|---|
| `none` | nothing | | |
| `basic` | HTTP Basic through an Authorization Manager | | `BOOMI_TEST_TARGET_BASIC_AUTH_USER`, `BOOMI_TEST_TARGET_BASIC_AUTH_PASS` |
| `api_key` | an API key in a header (default) or the query string | `api_key_header` (default `x-api-key`), `api_key_in` (`header` or `query`), `api_key_param` (default `api_key`), `api_key_env` | `BOOMI_TEST_TARGET_API_KEY` |
| `api_key_signature` | key plus signature in the query string | `signature_algorithm` (`sha256` default, or `md5`), `api_key_param`, `signature_param` (default `sig`), `api_key_env`, `api_secret_env` | `BOOMI_TEST_TARGET_API_KEY`, `BOOMI_TEST_TARGET_API_SECRET` |
| `bearer` | `Authorization: Bearer <token>` with a fixed token | `token_env` | `BOOMI_TEST_TARGET_BEARER_TOKEN` |
| `oauth2_client_credentials` | a token fetched from an identity provider and refreshed | `oauth:` block (7.3) | `BOOMI_TEST_OAUTH_CLIENT_ID`, `BOOMI_TEST_OAUTH_CLIENT_SECRET` |

Validation rejects settings that do not apply to the chosen type (for example `api_key_in` with `basic`), and any `*_env` value that is not an upper-case variable name.

### 7.1 API key in the query string

With `api_key_in: query`, `?api_key=<value>` (or `&` if the path already has a query) is appended to every request path at plan-generation time. No header is added.

### 7.2 Signed requests

`api_key_signature` appends `api_key=<key>&sig=<digest>` to every request. The digest is the lowercase hex of `SHA-256(key + secret + epoch seconds)` (or MD5 with `signature_algorithm: md5`), recomputed on every request so the timestamp is always fresh. It is generated by a single JMeter `__groovy()` expression, with no commas because JMeter would read them as function arguments. This follows the documented Boomi Cloud API Management (Mashery) rule for the `apiKeyAndSecret_SHA256` scheme (digest of key, secret and a Unix timestamp in seconds, accepted within a few minutes of the gateway's clock). **It is verified only against the bundled sample target, not against a real area** (section 7.5).

### 7.3 OAuth 2.0 client credentials

Profile keys under `target.oauth`: `token_url` (required, full http or https URL), `scope`, `audience`, `client_auth` (`basic`, the default, sends the client id and secret as an HTTP Basic header; `post` sends them as form fields), `refresh_skew_sec` (default 30, non-negative integer), `client_id_env`, `client_secret_env`. Unknown keys are rejected.

**Before any load starts**, `ramp.py` runs one token request with `curl` inside a `jmeter` container, using the container's environment. The outcomes and messages:
- credentials not set: names the two variables to add to `.env`.
- token URL unreachable: says so, and reminds that a host-machine URL must use `host.docker.internal`.
- HTTP error: shows the status and the first 300 characters of the response and tells the user to check client id, secret, scope and token URL.
- HTTP 200 without `access_token`: says so.
- success: prints the token lifetime, and warns if the lifetime is not longer than `refresh_skew_sec` (every request would refetch).
A failed pre-check exits with code 1 before any load.

**During the run**, the generated plan contains a JSR223 pre-processor (Groovy) at thread-group level, so it runs before every request. It:
- reads `oauth_token` and `oauth_expires_at` from JMeter properties (shared by all threads in the JVM);
- if the token is missing or within `refresh_skew_sec` of expiring, takes a lock and fetches a new one with a plain HTTP POST (`grant_type=client_credentials`, optional `scope` and `audience`; 10 s connect and 15 s read timeouts); a second thread that waits on the lock re-checks and skips a redundant fetch;
- parses `access_token` and `expires_in` from the JSON with regular expressions (default lifetime 300 s if `expires_in` is absent);
- on failure, logs the error and sets `oauth_retry_after` five seconds ahead, so a broken token endpoint is not hit by every thread on every request;
- the requests carry `Authorization: Bearer ${__P(oauth_token)}`.

Each parallel JMeter instance fetches its own token. Because the fetch is a pre-processor and not a sampler, token requests are not counted in latency or throughput.

### 7.4 Okta notes

Create an API Services app (client credentials grant). Use a custom authorization server (the built-in `default` works) with a scope and an access-policy rule that allows the client credentials grant. The token URL is `https://<org>.okta.com/oauth2/<authServerId>/v1/token`. Okta's organization authorization server does not issue tokens for your own APIs. Identity providers often rate-limit the token endpoint; use a non-production tenant. Not tested against a real Okta tenant.

### 7.5 Boomi Cloud API Management (Mashery)

Mapping from an endpoint's authentication setting to a profile:

| Endpoint setting | Profile `target:` |
|---|---|
| `apiKey`, key as `?api_key=` | `auth: api_key`, `api_key_in: query` |
| `apiKey`, key in a header | `auth: api_key`, `api_key_header: <header name>` |
| `apiKeyAndSecret_SHA256` | `auth: api_key_signature` |
| `oauth` | `auth: oauth2_client_credentials` with the service's token URL |

Use the area's traffic domain as `host` (for example `yourcompany.api.mashery.com`), `protocol: https`, `port: 443`, and the endpoint's path alias in request paths. Facts to pass on:
- A package plan enforces queries per second and quota per key. A test above those limits gets 403 or 429 and a rising error rate, which looks like failure, not slowness. Use a dedicated test key and confirm with the API owner first.
- The key travels in the URL for the query-string schemes, so it can appear in gateway logs. The toolkit itself keeps URLs out of its results files.
- The Cloud API Management Boomi skill describes the signed scheme loosely as "HMAC-SHA256"; the implementation follows the digest-of-key-secret-timestamp rule instead. If a real area rejects signed calls (403), check the algorithm and the clock on the machine running the test. This is the main item to verify when a real area is available.
- Keyless endpoints are possible in Cloud API Management only through a special setup (allow missing key plus a `noapikey` package key); not specifically supported.

### 7.6 Credentials in practice

All variables live in `.env` (gitignored; copy `.env.example`). Docker Compose passes `.env` into the JMeter containers (`env_file`, optional). Compose also sets public demo defaults for the bundled samples so they work with no `.env`: `BOOMI_TEST_DB_USER/PASSWORD` (`loadtest`/`loadtest`), `BOOMI_TEST_OAUTH_STUB_CLIENT_ID/SECRET` (`loadtest-client`/`loadtest-secret`) and `BOOMI_TEST_CAM_STUB_KEY/SECRET` (`cam-test-key`/`cam-test-secret`). After editing `.env`, run again: containers read it at start.

---

## 8. Test plans and plan generation

### 8.1 Hand-built plans in `test-plans/`

| File | Purpose |
|---|---|
| `rest/starter.jmx` | One-shot REST flow: list customers, extract the first `customer_id`, get that customer. Two assertions on the detail call (status 200, and the body echoes the extracted id, which catches a silent extractor failure). Includes a 15 s pause before the listener. |
| `graphql/starter.jmx` | Same shape for GraphQL: list, extract `customerId`, detail; asserts the body echoes the id (GraphQL returns 200 even on a miss). 15 s pause. |
| `soap/smoke-test.jmx` | SOAP correlation test against the public `dneonline.com` calculator: `Add(5,7)`, extract the result, add 3, assert the final sum is exactly 15. 15 s pause. |
| `rest/ramp.jmx`, `graphql/ramp.jmx`, `soap/ramp.jmx` | Sustained-load variants for the orchestrator. No pause. Thread count and duration come from `-JTHREADS` and `-JDURATION`. They carry the Prometheus listener with five collectors. |
| `_infra-check/empty.jmx` | An empty plan used by the smoke test to prove metrics reach Prometheus. |

The starters' 15 s pause exists only so a 1-to-2-request demo survives long enough for Prometheus's 5 s scrape to catch it. Delete it when cloning a starter for a real test.

Ramp plan thread group: `num_threads = ${__P(THREADS,1)}`, ramp time 1 s, scheduler on, `duration = ${__P(DURATION,60)}`, on sample error "continue".

### 8.2 Generated plans

When a profile has `target` and `requests`, `build_plan_from_profile()` reads the profile's `test_plan` as a template, keeps its thread group and Prometheus listener, removes everything in between and generates, in order:
1. HTTP Request Defaults (host, port, protocol).
2. Auth: an HTTP Authorization Manager for `basic`; header entries for `api_key` (header mode), `bearer`; for `oauth2_client_credentials` a JSR223 pre-processor plus an `Authorization: Bearer ${__P(oauth_token)}` header.
3. A Header Manager (auth headers, and `Content-Type: application/json` if any request has a body).
4. A Constant Throughput Timer if any step has `rps` or `tps`.
5. One HTTP sampler per request. Query-string auth is appended to each sampler's path. A request with `extract` gets a JSON post-processor (`match_numbers 1`, default `NOT_FOUND`). A Constant Timer follows a request when it has a think time.
6. The Prometheus listener and its tree.

The result is written to `test-plans/generated/<run_id>.jmx` (gitignored) and that path is what the containers run. For `jdbc` it generates a JDBC Connection Configuration (per-thread connections, pool max 0, 10 s timeout, a `SELECT 1` check, user and password from the environment) and one JDBC sampler per query (prepared when it has params; "Count Records" result handling so the load generator does not materialise result sets).

### 8.3 Authoring plans by hand

Two ways, same shared `test-plans/` folder: a local JMeter (needs the Prometheus plugin jar installed, or plans fail to open with `CannotResolveClassException`), or the `jmeter-gui` container at http://localhost:6080/vnc.html. Real load always runs through the headless service (`-n`), never the GUI, because GUI mode consumes CPU that skews results.

Known cosmetic quirk: selecting the Prometheus Listener node in the JMeter GUI shows a dismissible error (a plugin 0.7.1 GUI registration issue). It does not affect running. Copy the listener from an existing plan instead of configuring one from scratch.

---

## 9. Metrics model

### 9.1 Collectors

Defined in each plan's Prometheus Listener, listening to `samples`:

| Metric | Type | Labels | Notes |
|---|---|---|---|
| `jmeter_requests_total` | counter | `label`, `code` | every sample |
| `jmeter_response_time_ms` | histogram | `label`, `code` | buckets 1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 250, 500, 1000, 2500, 5000, 10000 ms |
| `jmeter_failures_total` | counter | `label`, `code` | assertion failures including those that return HTTP 200 |
| `jmeter_connect_time_ms` | histogram | `label`, `code` | ramp plans only |
| `jmeter_latency_ms` | histogram | `label`, `code` | time to first byte; ramp plans only |
| `jmeter_threads{state}` | gauge | `state` | the plugin's own; no `run_id` or `step_id` label |

The base starters were changed on 2026-11-06 to the same fine buckets (before that, starters used 50, 100, 250, 500, 1000, 2500, 5000, 10000).

### 9.2 Where `run_id` and `step_id` come from

For ramp runs, not from JMeter. Prometheus's file-based discovery attaches the labels declared in each target file to every sample scraped from that container: `run_id` (a timestamp such as `20261107-125037`) and `step_id` (`step-01`, ...). For `run-test.sh` runs the `run_id` comes from `-JRUN_ID`.

### 9.3 Prometheus configuration

Scrape and evaluation interval 5 s. Two jobs:
- `jmeter`: static target `jmeter:9270`, for the base stack.
- `jmeter-ramp`: `file_sd_configs` over `/etc/prometheus/targets/*.json`, refresh every 5 s.

JMeter's Prometheus listener binds to `127.0.0.1` by default; the image overrides it to `0.0.0.0:9270` (`jmeter.properties.append`). Without that, Prometheus on another container can never reach it, silently.

Target file shape: `[{"targets": ["<container>:9270"], "labels": {"run_id": "...", "step_id": "..."}}]`, named `<run>-<step>-<index>.json`, container named `jmeter-ramp-<run>-<step>-<index>`. Files are deleted when the step ends.

A scrape job with zero discovered targets does not appear in Prometheus's `/api/v1/targets` list. That is expected until the first target file exists (check `/api/v1/status/config` instead).

### 9.4 Histogram limits

Percentiles are bucket estimates, so their resolution is the bucket spacing. With 1 ms to 10 s buckets, fast APIs resolve well. Baselines saved before 2026-10-25 used coarse 50 ms-and-up buckets and should be re-saved. Averages and p99 were trustworthy at any bucket setting.

---

## 10. The orchestrator (`ramp.py`)

### 10.1 Command line

`ramp.py [--yes] [--validate] [--save-baseline] [--try-request] [--summary-json <path>] <profile.yaml>` (`ramp.sh` is the wrapper). `--yes` skips the large-run confirmation. `--validate` checks and exits 0. `--save-baseline` records this run as the baseline named in the profile's `regression` block (needs one). `--try-request` sends the first request once and prints a JSON result, then exits 0 (ok) or 1. `--summary-json` writes a machine-readable result (used by the console).

### 10.2 The run, in order

1. Load and validate the profile. Invalid: print the problems, exit 1.
2. If `--try-request`: run it and exit.
3. If `--validate`: print "Profile is valid" and exit 0.
4. Create `run_id = YYYYMMDD-HHMMSS`. Print `Run ID: ...`.
5. If the profile has `requests`: generate the plan and print its path.
6. If auth is OAuth: run the pre-check (10.7). Failure exits 1.
7. Print short-step warnings: a step too short to leave enough measured time after the warm-up and the 5 s settle margin (the message says steps shorter than about 20 s can be incomplete or empty).
8. Large-run confirmation (10.8).
9. For each step: run it (10.3), then evaluate the stop rule (10.4). A triggered stop rule ends the loop.
10. Print `Done`, with each step's instance exit codes.
11. For each step, query Prometheus for report rows (10.5); scan raw results for slow requests (if a threshold is set) and for failures.
12. Print the summary report, the failure list, a no-data warning if any step had no samples, and the slow-request table.
13. Regression gate (10.6).
14. Choose the exit code (Appendix A). Write the summary JSON if asked. Exit.

### 10.3 One step

`run_step`:
- Compute duration, instances, threads and the request-rate cap (10.1; section 6.6).
- Print the launch line, including both TPS and requests per second when a TPS target is set.
- Post a Grafana "step started" annotation.
- Launch `instances` containers in parallel. Each launch first removes any stale container with the same name (a real hazard after an interrupted run), writes the Prometheus target file, then runs: `docker-compose run --rm --use-aliases --name <container> jmeter-ramp -n -t /test-plans/<plan> -l /test-plans/results/<run>-<step>-<index>.jtl -JTHREADS=<n> -JDURATION=<s> [-JRPS_PER_MIN=<n>] -Jjmeter.save.saveservice.url=false`. The last option keeps request URLs (which can hold an API key) out of the raw results file. `--use-aliases` is required (section 18).
- After `min(duration/2, 10)` seconds, sample CPU: sum `docker stats` CPU percent over the containers; if it exceeds 85% of the host's cores, print a warning that the load generator looks saturated and flag the step's rows.
- Wait for every container. Post a "step ended" annotation. Delete the target files. Warn if any instance exited non-zero.

### 10.4 Stop rule

After each step, query Prometheus for the window `[step_start + warmup, step_end - 5 s]`, evaluated at `step_end - 5 s` (10.9 explains the 5 s):
- error rate = `increase(jmeter_failures_total)` over `increase(jmeter_requests_total)`, summed over the step's series;
- average response = `sum(increase(response_time_sum)) / sum(increase(response_time_count))`.
If error rate exceeds `max_error_rate_pct` or the average exceeds `max_avg_response_ms`, print the reason and stop. Exit code 2.

### 10.5 The summary report

For each step, one row per sampler label, from Prometheus over the same window:
- samples = `sum(increase(requests_total))`; error % = failures over samples; throughput = samples over window seconds;
- average from the sum and count series;
- median, p90, p95, p99 from `histogram_quantile` over `sum(increase(response_time_ms_bucket)) by (le)`.
Every query wraps in `sum(...)`. This matters: without it a query scoped to a run returns one series per instance and the report picks one arbitrarily (section 18). If a step has no Prometheus data, a `(no data)` row is added with a warning, so a step never vanishes silently.

Raw results (`.jtl`) are read for two things only: the top failure messages per step, and slow requests above the threshold. A `.jtl` read does not depend on scrape timing.

### 10.6 Regression gate

Baseline file `baselines/<name>.json`: `run_id`, `saved_at`, `steps` (instances and threads per step) and `rows` keyed `step_id|label` with `samples`, `p95_ms`, `error_pct`, `throughput`.
- `--save-baseline` saves only if the run had no stop-rule trigger and no failed instance (otherwise it prints why it did not).
- Without a baseline file, the check is skipped with a note.
- Otherwise compare each row: **REGRESSION** if the p95 increase exceeds both `max_p95_increase_pct` (default 25) and `min_p95_delta_ms` (default 10), or the error rate rose by more than `max_error_rate_increase_pct` (default 1 point). **NO DATA** if the row has no samples (counts as a failure, because no data cannot confirm "no regression"). Rows can also be `new` or `missing`. A warning is printed if the baseline was recorded with different instances or threads. Both a percentage and an absolute bar are required so 3 ms to 6 ms (+100%) is not flagged as noise.
- Any REGRESSION or NO DATA exits 4.

### 10.7 The OAuth pre-check and the one-request test

Both run a small shell script with `curl` inside a `jmeter` container (`docker-compose run --rm -T --no-deps --entrypoint sh jmeter -c ...`), passing profile values as `-e` environment variables and reading secrets from the container's own environment, so secrets never appear on a command line. The one-request test builds the URL, applies the profile's auth (including fetching an OAuth token, computing a signature, or appending the query-string key), sends the first request and returns JSON: `ok`, `status`, `seconds`, the first 1,200 characters of the body, `url`, `request`, `auth`, `message`. It declines HTTP profiles whose first request uses a chained value, and database profiles.

### 10.8 Confirmation gate

Before anything launches, every step is scanned. If any step has more than 4 instances, the user must confirm. With `--yes` it prints a notice and proceeds. With no terminal and no `--yes` it exits 1 with an explanation. (The console applies a broader set of reasons; section 11.5.)

### 10.9 Timing: why queries run 5 seconds early

Prometheus marks a scrape target stale within about 3 to 4 seconds of its last scrape once its file is removed. `step_end` is captured right as the container dies, which lands on that edge, so querying at `step_end` could return nothing and the step's row would silently vanish. `QUERY_SETTLE_SEC = 5` (one scrape interval) backs the query instant off to before the cutoff, and the window's upper bound is trimmed to match. This was found when the first four-step run lost its last step (6 of 8 rows).

### 10.10 Interrupts

SIGINT or SIGTERM during a run force-removes the current step's containers, deletes their target files and exits 130. Earlier steps stay visible in Grafana.

### 10.11 Grafana annotations

At each step start and end, `ramp.py` posts to Grafana's `/api/annotations` with tags `ramp`, the run id and the step id, using the default `admin`/`admin` login (hard-coded). A failure only prints a warning.

### 10.12 The summary JSON

`run_id`, `exit_code`, `rows`, `slow_requests` (capped at the slowest 200), `slow_request_count`, `regression` (baseline name, status `none`, `saved`, `not_saved`, `no_baseline`, `passed` or `failed`, and per-row results), `failures` (per step: total, failed, top messages), `no_data_steps`.

### 10.13 Exit codes

See Appendix A.

---

## 11. The web console

### 11.1 What it is

`./console.sh` starts a Python standard-library HTTP server (default `127.0.0.1:8800`) that serves one HTML page and a JSON API. It runs `ramp.py` as a subprocess, so results are identical to the command line. State lives on disk, so the console can be restarted and a run in progress is picked up again.

Options: `--host`, `--port` (8800), `--grafana-url`, `--allow-host` (repeatable), `--runs-dir`. Environment: `CONSOLE_TOKEN` (also read from `.env` by `console.sh`), `CONSOLE_NOTIFY_WEBHOOK`, `CONSOLE_PROMETHEUS_URL` (default `http://localhost:9090`), `CONSOLE_GRAFANA_HEALTH_URL` (default `http://localhost:3004/api/health`).

### 11.2 Security model

- **Loopback by default.** Binds `127.0.0.1`. Binding to any other address is refused unless `CONSOLE_TOKEN` is set.
- **Token rules.** A token must be at least 16 characters. It is checked in constant time. A wrong token waits half a second before the error. The sign-in sets an `HttpOnly`, `SameSite=Strict` cookie; an `Authorization: Bearer` header also works.
- **Host check.** On a loopback bind only `localhost`, `127.0.0.1`, `::1` are accepted as the Host header, which blocks DNS-rebinding. Extra names can be allowed with `--allow-host`.
- **Origin check.** A non-GET request that carries a browser `Origin` must match the server's own host. Requests without an Origin (curl, scripts) are allowed.
- **Safe names.** Profile names must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$` and cannot end in `.`; no path separators. Run ids must match `run-[0-9-]+`. Static files are served only from `console/static/` (path-escape checked).
- **Request size.** JSON bodies are capped at 1 MiB and must be `application/json`.
- **Responses** send `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.
- **Secrets.** The checklist reports whether `.env` variables are set by name only. Values are never returned.
- **What it can do.** It can start load against real systems, which is why a token is mandatory off localhost and why risky runs ask for confirmation (11.5).

### 11.3 Run lifecycle and storage

A run lives in `console/runs/<run>/`: `state.json`, `log.txt`, `summary.json`, `profile.yaml` (a snapshot, so later edits do not change a run in progress) and optionally `note.txt`. Run ids are `run-YYYYMMDD-HHMMSS` (with `-2`, `-3`, ... if two start in the same second).

The console starts `ramp.py --yes --summary-json <run>/summary.json [--save-baseline] <snapshot>` in its own session. State fields: `id`, `profile`, `save_baseline`, `status`, `exit_code`, `pid`, `started_at`, `ended_at`, `stop_requested`, `total_sec`, `steps`, `warmup_sec`, `requests_per_iteration`. Derived when read: `ramp_run_id` (parsed from the log), `current_step` (last "[step-NN] launching" line), `note`, `summary`.

Statuses: `running`, `passed` (exit 0), `error` (1), `stop_rule` (2), `instance_failed` (3), `regression` (4), `no_data` (5), `stopped` (the user stopped it), `lost` (the process vanished with no exit code, for example the console was killed and the run died). Only one run can be active at once (HTTP 409 otherwise). **Stop** sends SIGTERM, which runs `ramp.py`'s cleanup.

A finished run's `ended_at` is taken from the modification time of its summary file, not from when the console next noticed it, so a run nobody was watching still reports the right duration.

### 11.4 Features

**Profile list.** Filterable, each with a small thumbnail of its load shape and a summary (phases, duration, peak rate). **New profile** opens an editor with a template.

**Load curve.** Selecting a profile draws its load as a stepped curve on a dark panel: time on the x-axis, target rate (TPS, req/s or threads) on the y-axis with whole-number ticks, each phase's value labelled when wide enough. It draws in once on selection. Facts beside it: duration, phases, peak rate, most concurrency, target, requests per iteration.

**Before you run** checklist: Prometheus reachable, Grafana reachable, credentials the profile needs are set in `.env` (names only; Compose-provided sample defaults count as set), the target.

**Test one request** sends the first request once with the profile's auth and shows the status, time and start of the body.

**Actions:** Run; Run and save as baseline (only if the profile has a baseline name; asks to confirm replacing it); Validate (uses `ramp.py`'s validator); Edit YAML (saving validates first and never overwrites without asking); Duplicate (opens the editor with a `-copy` name); Delete (not while that profile is running; its baseline and past runs stay); Form builder (opens `/builder.html`).

**Live run view.** The same curve with the executed part filled in (blue while running; green passed; amber for stop rule, stopped or lost; rose for failure) and an orange "now" marker with a pulse. Live tiles: throughput in TPS (with the phase's target and the requests-per-second figure; profiles written in requests per second show requests per second first), average and slowest-5% (p95) latency, error rate, active threads. A progress bar and the current step. Numbers lag real time by a few seconds because Prometheus scrapes every 5 s.

**Outcome.** A plain-words banner, then tabs: Results (per phase and endpoint: samples, average, median, p90, p95, p99, errors, per-second rate, with "generator busy" flagged), Baseline check, Log. Failure lists show the top JMeter failure messages.

**Notes, export, report.** A note (up to 500 characters) per run. Export CSV of the results table. Download report: a single self-contained HTML page with the curve, results, baseline verdict and note.

**History.** All runs with status, duration and note. Filter by profile, status or note text. Tick two runs and Compare: p95, error rate and per-second throughput side by side with percentage change coloured (lower is better for latency and errors, higher for throughput).

### 11.5 Risk confirmation

A run needs `confirmed: true` (the console shows a "Check before running" dialog with the reasons) when any of these hold:
- any step launches more than 4 instances;
- peak TPS is above 100, or (if no TPS) peak requests per second is above 200;
- the target host is not one of `localhost`, `127.0.0.1`, `host.docker.internal`, `rest-target`, `graphql-target`, `oauth-target`, `postgres-target`.
The server answers HTTP 428 with the reasons until confirmed.

### 11.6 Live metrics queries

Run id is validated against `^[0-9]{8}-[0-9]{6}$` before use in a query. Windows: 15 s for rates, 30 s for the p95 histogram.
- throughput (req/s) `sum(rate(jmeter_requests_total{run_id=...}[15s]))`; TPS is that divided by `requests_per_iteration` (stored when the run starts; runs started before that field existed assume 1);
- error % `100 * (sum(rate(failures[15s])) or vector(0)) / sum(rate(requests[15s]))`;
- p95 `histogram_quantile(0.95, sum by (le) (rate(response_time_ms_bucket[30s])))`;
- average from sum and count rates;
- active threads `sum(jmeter_threads{state="active"})`.
Values are null until Prometheus has scraped, and show as a dash.

### 11.7 Notification

If `CONSOLE_NOTIFY_WEBHOOK` is set, when a run finishes the console posts `{"text": "Load test <profile> finished: <status> after m:ss."}` to it (Slack and Microsoft Teams incoming webhooks both accept this). Best effort, off by default.

### 11.8 The form builder

`ramp-profile-builder.html` is a standalone page (works from the file system, no server). It collects target, auth (including query-string key, signed key, and OAuth fields), requests, steps, stop rule and regression settings, validates in the page the same way `ramp.py` does, and shows live YAML to copy or download. When served by the console at `/builder.html`, a **Save to console** button writes the profile straight into the list (asks before overwriting). It cannot load an existing profile back into the form.

### 11.9 Front-end design

One HTML file with inline CSS and JavaScript, no framework, no build, no external requests. Fonts (Bricolage Grotesque for titles and numbers, Figtree for the interface; both SIL OFL 1.1) are served from `console/static/fonts/`. Light and dark themes follow the browser; `?theme=light` forces light. The load curve panel is deliberately the one heavy visual element. Sections are open rather than boxed in identical cards. Orange is reserved for the live "now" marker. The page polls every 1.5 s while a run is active, 8 s otherwise, 5 s after an error. Keyboard focus is visible, reduced-motion is respected, and the layout collapses to one column below 960 px. A profile can be opened directly with `#profile=<name>` (add `&edit` to open the editor).

### 11.10 API

See Appendix E.

---

## 12. Grafana

### 12.1 Provisioning

The image bakes in a datasource (name Prometheus, uid `prometheus`, URL `http://prometheus:9090`), a dashboard provider and the dashboard JSON. Login `admin`/`admin` (change it if you expose the port). **Persistent-state trap:** Grafana keeps its own database in the `grafana-data` volume. After changing dashboard or provisioning files, rebuilding the image is not enough because Grafana already imported the old version. Stop and remove the grafana container and remove the volume (`docker volume rm boomi-loadtest_grafana-data`), then bring it back up.

### 12.2 Dashboard

"Boomi Performance Testing — JMeter Overview", uid `boomi-perf-jmeter`, 16 panels, three variables:
- `run_id`: `label_values(jmeter_requests_total, run_id)`
- `step_id`: `label_values(jmeter_requests_total{run_id=~"$run_id"}, step_id)`
- `tps_label`: `label_values(jmeter_requests_total{run_id=~"$run_id", step_id=~"$step_id"}, label)`, the request that starts each iteration, used for the TPS panel.

| Panel | Type | Query (abridged; selectors filter on `$run_id`, `$step_id`) |
|---|---|---|
| Active Threads | stat | `sum(jmeter_threads{state="active"})` |
| Total Requests | stat | `sum(increase(jmeter_requests_total[$__range]))` |
| Throughput (RPS) | stat | `sum(rate(jmeter_requests_total[1m]))` |
| Error Rate | stat | `100 * sum(rate(failures[5m])) / sum(rate(requests[5m]))` |
| Throughput (TPS) | stat | `sum(rate(jmeter_requests_total{label="$tps_label"}[1m]))` |
| p90, p95, p99 Response Time | stat (3) | `histogram_quantile(q, sum(rate(response_time_ms_bucket[5m])) by (le))` |
| Request Rate | timeseries | `sum(rate(requests[1m])) by (run_id)` |
| Response Time Percentiles | timeseries | p50, p90, p95, p99 over `[1m]` |
| Error Rate | timeseries | `sum(rate(failures[1m])) by (run_id)` |
| Active Threads | timeseries | `sum(jmeter_threads{state="active"})` |
| Connect Time vs Latency vs Response Time (p95) | timeseries | three `histogram_quantile(0.95, ...)` over the connect, latency and response histograms |
| Endpoint-Level Summary | table | four instant queries merged by label: samples, average, p95, error rate |
| Top Slowest Endpoints (p95) | bar gauge | `topk(10, histogram_quantile(0.95, sum(increase(bucket[$__range])) by (le, label)))` |
| Error Tracking by Code | timeseries | `sum(rate(requests[1m])) by (label, code)` |

Notes: an "Error Rate" panel showing "No data" on a clean run is correct, because the failures counter series does not exist until the first failure. The "Active Threads" panel uses `sum(...)` because the plugin's gauge has no `run_id` label and each container reports its own value; without the sum it renders as scattered identical dots. The endpoint table and slowest-endpoints panels use instant queries, so viewed within a few seconds of a run finishing they can briefly show nothing.

---

## 13. Docker images and publishing

### 13.1 The images

| Image | Built from | Contents | Published |
|---|---|---|---|
| `pythonicshahdev/boomi-loadtest-jmeter` | `docker/jmeter/Dockerfile` on `eclipse-temurin:17-jre-jammy` | JMeter 5.6.3, `jmeter-prometheus-plugin` 0.7.1, PostgreSQL JDBC 42.7.4, metrics bound to `0.0.0.0:9270`, entrypoint `jmeter`, workdir `/test-plans`; installs `curl` | yes |
| `pythonicshahdev/boomi-loadtest-grafana` | `docker/grafana/Dockerfile` on `grafana/grafana:latest` | datasource, dashboard provider, dashboard JSON | yes |
| `pythonicshahdev/boomi-loadtest-graphql-target` | `docker/sample-services/graphql-strawberry/Dockerfile` on `python:3.12-slim` | FastAPI + Strawberry GraphQL sample on port 8001 | yes |
| `boomi-jmeter-gui:dev` | `docker/jmeter-gui/Dockerfile`, `FROM` the JMeter image (build arg `JMETER_IMAGE`) | Xvfb, fluxbox, x11vnc, noVNC/websockify, supervisor, JMeter Studio 1.0.2 theme plugin | no (built locally; authoring only) |
| `boomi-loadtest-oauth-target:dev` | `docker/sample-services/oauth-stub/Dockerfile` on `python:3.12-slim` | auth sample (section 14) | no (built locally) |

Other images used as-is: `prom/prometheus:latest`, `postgres:16-alpine`, and `pythonicshahdev/fastapi-commercial-source:latest` for `rest-target`.

### 13.2 Tags and platforms

Each published image has `latest` and a dated tag (`YYYY-MM-DD`; first release `2026-11-06`), and is multi-arch: `linux/amd64` and `linux/arm64`. Overviews for the three repos are in `docs/dockerhub/` and are pasted into Docker Hub by hand.

### 13.3 Publishing procedure

On this machine `docker compose` and `docker buildx` as top-level subcommands are broken; use the standalone `docker-compose` and the `multiarch` buildx builder (`docker buildx use multiarch`), which can build amd64 under emulation.
```
docker buildx build --platform linux/amd64,linux/arm64 \
  -t pythonicshahdev/boomi-loadtest-<name>:latest -t pythonicshahdev/boomi-loadtest-<name>:<date> --push <context>
```
Use `${img}:latest` with braces in zsh (section 18). New Docker Hub repos were created private by default and must be set public. To re-tag without rebuilding: `docker buildx imagetools create -t <img>:latest <img>:<date>`.

### 13.4 Compose image behaviour

`docker-compose.yml` names the published images and also has `build:` contexts, so `docker-compose pull` fetches and `docker-compose build` builds locally; either way the name is the same. `jmeter-gui` builds on top of whichever JMeter image exists locally, so the JMeter image is built or pulled first.

---

## 14. Sample services

| Service | Port (host:container) | Serves |
|---|---|---|
| `rest-target` | 8010:4005 | A FastAPI customer service from a separate image (`pythonicshahdev/fastapi-commercial-source`): `GET /ping`, `GET /customers?limit=n`, `GET /customers/{id}`. |
| `graphql-target` | 8011:8001 | `POST /graphql` with queries `customers(limit)`, `customer(customerId)`, `products(limit)`, `product(materialNumber)`; `GET /ping`. Fake data from Faker. |
| `oauth-target` | 8012:8000 | See below. |
| `postgres-target` | 5439:5432 | PostgreSQL 16 with `customers` (100,000 rows, index on city) and `orders` (300,000 rows), created once by `init.sql` when the volume is first created. Login `loadtest`/`loadtest`. Has a health check. |
| SOAP | public | The public calculator `http://www.dneonline.com/calculator.asmx`. No SOAP server is bundled. |

**`oauth-target`** (`docker/sample-services/oauth-stub/app.py`, FastAPI):
- `POST /token`: OAuth client credentials. Accepts HTTP Basic or form credentials. Valid client `loadtest-client` / `loadtest-secret` (env `OAUTH_STUB_CLIENT_ID/SECRET`). Issues random tokens that live `OAUTH_STUB_TOKEN_TTL_SEC` seconds (default 30), returned as `expires_in`. 401 `invalid_client` for wrong credentials; 400 for another grant type.
- `GET /customers`, `GET /customers/{id}`: need a valid unexpired bearer token, else 401 `invalid_token`.
- `GET /key/customers`, `/key/customers/{id}`: need `?api_key=` equal to `cam-test-key` (env `CAM_STUB_API_KEY`); else 403.
- `GET /signed/customers`, `/signed/customers/{id}`: need `api_key` and `sig`; `sig` must be the hex digest (SHA-256 if 64 characters, MD5 if 32) of key + secret (`cam-test-secret`, env `CAM_STUB_API_SECRET`) + epoch seconds within 300 s (env `CAM_STUB_SKEW_SEC`). The current second and four neighbours are checked first, then the full window.
- `GET /stats`: counters `tokens_issued`, `token_requests_rejected`, `api_ok`, `api_rejected`, `cam_ok`, `cam_rejected`, plus `token_ttl_sec`. This is how to confirm tokens were refreshed and nothing was wrongly rejected.
- `GET /ping`.
Credentials are public demo values.

---

## 15. Setup and operations

### 15.1 Prerequisites

Docker with the `docker-compose` command (the scripts call it directly; Docker Desktop includes it), and Python 3.9 or newer.

### 15.2 Option A: the setup script

`./setup.sh [--no-smoke] [--build]` is safe to re-run. It checks prerequisites with clear messages, creates `.env` from `.env.example` if absent, pulls the three published images (falling back to a local build if the pull fails, or with `--build`), builds `jmeter-gui`, starts `prometheus grafana rest-target graphql-target`, creates `.venv-ramp` and installs `requirements-ramp.txt`, and runs the smoke test.

### 15.3 Option B: manual steps

```
cp .env.example .env
docker-compose pull jmeter grafana graphql-target     # or: docker-compose build
docker-compose build jmeter-gui
docker-compose up -d prometheus grafana rest-target graphql-target
./smoke-test.sh
python3 -m venv .venv-ramp && source .venv-ramp/bin/activate && pip install -r requirements-ramp.txt
./ramp.sh profiles/rest-minimal.yaml
```
(The sample services `oauth-target` and `postgres-target` are started on demand: `docker-compose up -d oauth-target`.)

### 15.4 The smoke test

`./smoke-test.sh` brings the services up, waits up to 60 s for each local service, then checks: compose up completed; `rest-target /ping`; `graphql-target` query; the public SOAP demo; Prometheus healthy; Grafana healthy; the `jmeter-gui` noVNC page; the JMeter metrics port is reachable from another container (it starts the empty plan with `--use-aliases` and fetches `jmeter:9270/metrics` from the Prometheus container, bypassing Prometheus itself); and Prometheus actually scraped the metrics (a range query over the last two minutes, because an instant query goes empty as soon as a target disappears). Nine PASS lines expected.

### 15.5 Running tests

- Console: `./console.sh` then http://localhost:8800.
- Command line: `./ramp.sh profiles/<name>.yaml`; `--validate`, `--save-baseline`, `--try-request`, `--yes` as needed.
- Starter plan once: `./run-test.sh rest/starter.jmx` (prints the `RUN_ID` to filter by in Grafana). If you call `docker-compose run jmeter ...` yourself, add `--use-aliases`.

### 15.6 After a restart

After Docker or the machine restarts, the long-running services may have exited (a past restart left Prometheus, Grafana and the targets at exit code 255) and `ramp.py` then reports "NO DATA". Bring them back: `docker-compose up -d prometheus grafana rest-target graphql-target` (plus `oauth-target` and `postgres-target` if used). Prometheus and Grafana data persist in named volumes. A Boomi or direct-to-backend profile also needs its target running (for example the local Atom on port 8078, and any SSH tunnel).

### 15.7 Running on a VM

Ports bind to `127.0.0.1`; to reach them from elsewhere, change the `127.0.0.1:` prefix in `ports:` entries. Set a VNC password in `docker/jmeter-gui/supervisord.conf` (it runs with `-nopw`) first, because that desktop can see real credentials. The console refuses a non-loopback bind without `CONSOLE_TOKEN` (at least 16 characters, for example `openssl rand -hex 24`) and needs `--allow-host` for its public name. On Linux the JMeter containers run as root, so files in the bind-mounted `test-plans/` land root-owned; add `user: "${UID:-1000}:${GID:-1000}"` to the `jmeter` and `jmeter-gui` services if that matters. `host.docker.internal` works through `extra_hosts: host-gateway`.

### 15.8 Clean-up and housekeeping

Generated plans, results files and run folders accumulate and are never pruned. Delete `test-plans/generated/`, `test-plans/results/*.jtl` and `console/runs/` as needed. To start Grafana and Prometheus fresh, remove the two volumes.

---

## 16. Security model

**Protections:**
- Every published port, and the console, bind to `127.0.0.1` by default.
- Secrets live in `.env` (gitignored), never in profiles; profiles hold only variable names, validated by pattern so a pasted secret is rejected.
- Secrets reach containers as environment variables and are never put on command lines; the checklist and responses never include values.
- JDBC is read-only unless `allow_writes: true`.
- The console: token for any non-local bind, constant-time comparison, `HttpOnly` `SameSite=Strict` cookie, Host and Origin checks, safe names, a body size cap.
- Risky runs ask for confirmation (large instance count, high rate, non-local target).
- Request URLs are kept out of raw results files, so a query-string key is not stored there.
- The history was scanned for secrets (no tracked `.env`, keys or credential assignments) before the repository was made public.

**Known exposures and caveats:**
- Grafana's default login is `admin`/`admin`, and `ramp.py` uses it hard-coded for annotations.
- The `jmeter-gui` desktop has no VNC password and can see the project's credentials; it is localhost-only by default.
- With the query-string schemes, the key is in the URL and can appear in the target's or gateway's logs.
- Sample-service credentials are public demo values.
- A local SSH tunnel used during the Boomi runs (port 4005) listened on all interfaces, which was flagged to the user.
- Docker Hub images and the GitHub repository are public; the Boomi-internal repository is internal.

---

## 17. Testing and verification

### 17.1 Automated tests

116 tests, run with `.venv-ramp/bin/python -m pytest tests -q` (needs `requirements-dev.txt`). They pass on a fresh clone.
- `tests/test_ramp.py` (70): profile validation (every rule in section 6), plan generation (auth, body, extractors, pacing, JDBC, OAuth script, query-string and signature auth), TPS/RPS conversion, baseline comparison, stop rule helpers, OAuth pre-check messages, the one-request test messages, and that launches keep URLs out of results.
- `tests/test_console.py` (46): safe names, summaries and risk reasons, access rules (loopback, Host, Origin, token), run lifecycle with a fake `ramp.py` (exit-code to status mapping, one run at a time, stop, history surviving a restart, lost runs), notes, delete, the end-time rule, the checklist, live metrics mapping, notifications, and the HTTP API end to end.
Prometheus is not mocked in unit tests for the report queries; those are verified live (17.2).

### 17.2 Live verification used

- The smoke test (nine checks) against a running stack.
- Real runs with the report cross-checked against the target's own counters: the OAuth sample issued the expected number of tokens for a 90 s run with 30 s tokens (a refresh about every 20 s with a 10 s skew), with zero rejected calls; the query-key and signature runs showed about 1,900 accepted calls each and none rejected.
- Signature correctness was checked independently with the host's `shasum` and `md5`, not only with the toolkit's own code.
- Query results checked against fresh data at explicit timestamps (not "now"), after early checks returned false empties from querying before a run started or after staleness.
- **Fresh-clone test**, repeated at each milestone: clone into an empty directory under the home folder (not `/tmp`, where Docker bind mounts fail), with an isolated compose project name (`COMPOSE_PROJECT_NAME=freshtest`), the user's own stack stopped to free ports, then `./setup.sh`, a first test, the test suite, the console. Passed on the pull path and the local-build path.
- **Browser checks** with headless Chrome: screenshots in light and dark themes during a live run and after one, and a scripted run of the console's interactive features (test request, notes, CSV, report, compare, filter).
- Anonymous checks from outside: GitHub repo page, raw files and redirect from the old name; Docker Hub repos public with overviews; registry manifests for `latest` and the dated tag listing both architectures.

### 17.3 Not verified

- Cloud API Management against a **real area** (only the bundled imitation).
- OAuth against a **real identity provider** such as Okta (only the bundled sample).
- The Docker Hub overviews as Docker Hub renders them.
- The form builder's layout visually (its generated YAML was checked).
- The console on a phone-width screen.
- Any load above 80 TPS against the Boomi Atom, and any breaking point.
- Running on a Linux host or a remote VM.

---

## 18. Pitfalls and lessons

Each was found and fixed (or documented) during real use.

1. **`docker-compose run` needs `--use-aliases`.** Without it Docker's DNS never registers the container under its service name, so Prometheus cannot resolve it; the test runs and reports success but exports no metrics. `run-test.sh` and `ramp.py` include the flag.
2. **`${__env(VAR)}` is not a real JMeter function.** It silently resolves to the literal placeholder. Use `${__groovy(System.getenv("VAR"))}`.
3. **Prometheus staleness.** A target is marked stale within about 3 to 4 seconds of its last scrape, not the five-minute lookback. Querying at the step's end can return nothing (section 10.9).
4. **Unsummed per-instance series.** A query scoped to a run returns one series per instance; picking the first made a 4-instance step report fewer samples than a 1-instance step. Every report query wraps in `sum(...)`.
5. **A step with no data vanished silently.** Fixed with a `(no data)` sentinel row and a warning, then exit code 5 for runs with no usable data.
6. **"Active Threads" looked broken.** The plugin gauge has no `run_id` label, so per-container values rendered as scattered dots. Fixed with `sum(...)`. Lesson: some "technically correct" panels are only caught by looking at the rendered result.
7. **Transaction Controller with `parent=true` is a one-way door.** With plugin 0.7.1 it hides per-sampler labels from Prometheus; a second inner listener silently cut throughput by about three orders of magnitude. Not used. TPS is derived from the flow's first request instead.
8. **PromQL regular expressions need escaping at three layers** (the regex, the PromQL string, the JSON file). Matching a literal `+` needs four backslashes in the dashboard JSON.
9. **Grafana's persistent volume masks dashboard changes** (section 12.1).
10. **The Prometheus listener binds to localhost by default.** Overridden to `0.0.0.0` in the image; otherwise a silent failure on both sides.
11. **A scrape job with no targets does not show in the targets list.** Check the config endpoint.
12. **"Error Rate: No data" on a clean run is correct.** The failures counter is created on the first failure.
13. **Histogram buckets cap percentile resolution** (section 9.4).
14. **The starter plans' 15 s pause** must be deleted for real tests.
15. **After a Docker restart services exit and runs report NO DATA** (section 15.6).
16. **`localhost` inside a container is the container.** Use `host.docker.internal`.
17. **JMeter treats commas as function arguments.** The signature expression is written with no commas.
18. **Docker bind mounts from `/tmp` fail on macOS** with "not a directory". Clone fresh-test directories under the home folder.
19. **zsh expands `$img:latest` as a modifier.** `:l` lowercases and leaves `atest`, which created junk repositories and left the real `latest` tag unpushed. Use `${img}:latest`. Also, an unquoted variable is not word-split in zsh, which made a `sed` loop do nothing.
20. **`docker logout` is not a harmless test.** It logs the user out. Check public visibility through the registry API instead, and use a separate `DOCKER_CONFIG` (which hides the buildx plugin) only with care.
21. **`pkill -f` can kill the shell that contains the pattern**, and the Python binary on macOS is named `Python`, so a lowercase pattern misses it.
22. **New Docker Hub repos default to private** and must be set public.
23. **A run's end time was when the console next looked.** Fixed to use the summary file's modification time.
24. **A fixed 5-second sleep raced Grafana's first boot** on a fresh volume in the smoke test; replaced by waiting up to 60 s per service.
25. **Query-string credentials leak into results files** through JMeter's URL column; the launch turns that column off.
26. **`docker compose` versus `docker-compose`.** The scripts call the standalone binary; `setup.sh` stops with a clear message if it is missing.
27. **A comment-only fix can still break a test.** A hard-coded dashboard path in the console (after the restructure) was caught only because a test read it.
28. **Historical specs and plans** in `docs/design/` use pre-restructure paths on purpose; a note says so.

---

## 19. Limitations and roadmap

### 19.1 Not built or not complete

- **Kubernetes and Helm: parked** (2026-11-07). The runner is Docker-specific (`docker-compose run`, container names, file-based discovery), so a chart needs a second runner that launches Jobs plus discovery through `PodMonitor` or annotations, not just templates. Preferred shape if revived: a runner interface with Docker and Kubernetes backends proved on kind or minikube, then a chart (console, Grafana, optional samples, Prometheus only if the cluster has none; profiles and baselines on a volume; credentials from Secrets; independent JMeter pods, not RMI master/slave). Questions to answer first: who runs it on Kubernetes, is there a cluster to test on, is there already Prometheus and Grafana, is Helm required or would Kustomize do. See `docs/roadmap.md`.
- **Scheduled runs** and regression alerts (needs a long-running console and a decision on where alerts go).
- **Loading an existing profile into the form builder.**
- **OAuth variants:** password and authorization-code grants, private-key JWT client authentication, mutual TLS.
- **Databases other than PostgreSQL** (each needs its driver in the image).
- **Boomi-side metrics** (runtime CPU and memory, execution records) beside the load numbers; **OpenTelemetry correlation** (deliberately left to the separate heavier project).
- **MCP Gateway targets** (depends on its transport).
- **Run retention** (no pruning).
- **True min and max latency** (histograms cannot provide them).
- **Per-request status assertions, CSV test data, scheduled or notified HTML reports.**
- **Multi-user features** (the console is a single-operator tool; one run at a time; one shared token).

### 19.2 Known limits to remember

- Load is generated on one machine; very high rates need a bigger machine, and the saturation warning says when the generator itself is the limit.
- `tps` and `rps` throttle but cannot raise load.
- Slow-request counts come from raw results; the total in the slow-request table is extrapolated from Prometheus.
- Instance output is discarded to `/dev/null` by `ramp.py` (diagnostics are lost when an instance fails; the JMeter log and the results file remain).
- `host_cpu_cores` and the CPU check use values that behave on macOS; untested on Linux.
- A real Cloud API Management area and a real identity provider have not been tested.

---

## Appendix A. Exit codes

| Code | Meaning | Status in the console |
|---|---|---|
| 0 | Clean run | `passed` |
| 1 | Invalid profile, failed OAuth pre-check, refused or aborted confirmation, or a usage error | `error` |
| 2 | A stop rule triggered | `stop_rule` |
| 3 | At least one JMeter instance exited non-zero | `instance_failed` |
| 4 | Regression against the baseline (including NO DATA rows) | `regression` |
| 5 | No usable data: a step recorded no measurable samples | `no_data` |
| 130 | Interrupted (SIGINT or SIGTERM) | `stopped` if the console asked |

The summary report prints in every case. Priority when several apply: 2, then 3, then 4, then 5.

## Appendix B. Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `BOOMI_TEST_TARGET_API_KEY` | auth `api_key`, `api_key_signature` | API key (name overridable with `api_key_env`) |
| `BOOMI_TEST_TARGET_API_SECRET` | auth `api_key_signature` | API secret (`api_secret_env`) |
| `BOOMI_TEST_TARGET_BASIC_AUTH_USER`, `_PASS` | auth `basic` | login |
| `BOOMI_TEST_TARGET_BEARER_TOKEN` | auth `bearer` | fixed token (`token_env`) |
| `BOOMI_TEST_OAUTH_CLIENT_ID`, `_SECRET` | auth `oauth2_client_credentials` | client login (`client_id_env`, `client_secret_env`) |
| `BOOMI_TEST_DB_USER`, `_PASSWORD` | JDBC | database login (`user_env`, `password_env`); Compose defaults `loadtest` |
| `BOOMI_TEST_OAUTH_STUB_CLIENT_ID`, `_SECRET` | sample profile | Compose sets `loadtest-client` / `loadtest-secret` |
| `BOOMI_TEST_CAM_STUB_KEY`, `_SECRET` | sample profiles | Compose sets `cam-test-key` / `cam-test-secret` |
| `CONSOLE_TOKEN` | console | access token, at least 16 characters; required off localhost |
| `CONSOLE_NOTIFY_WEBHOOK` | console | Slack or Teams incoming-webhook URL for finished-run messages |
| `CONSOLE_PROMETHEUS_URL`, `CONSOLE_GRAFANA_HEALTH_URL` | console | where the checklist and live numbers look |
| `OAUTH_STUB_CLIENT_ID`, `_CLIENT_SECRET`, `_TOKEN_TTL_SEC`, `_SCOPE` | oauth-target | sample's own settings |
| `CAM_STUB_API_KEY`, `_API_SECRET`, `_SKEW_SEC` | oauth-target | sample's Cloud API Management settings |
| `COMPOSE_PROJECT_NAME` | Docker Compose | overrides the `boomi-loadtest` project name (used for isolated test stacks) |

## Appendix C. Ports

| Host port | Service | Container port | Notes |
|---|---|---|---|
| 3004 | Grafana | 3000 | `admin`/`admin` |
| 9090 | Prometheus | 9090 | |
| 6080 | jmeter-gui (noVNC) | 6080 | `/vnc.html` |
| 8010 | rest-target | 4005 | |
| 8011 | graphql-target | 8001 | |
| 8012 | oauth-target | 8000 | started on demand |
| 5439 | postgres-target | 5432 | started on demand |
| 8800 | console | | host process |
| (internal) | JMeter metrics | 9270 | scraped by Prometheus only |

All published ports bind to `127.0.0.1`.

## Appendix D. Metric reference

See section 9.1. Labels on the request metrics: `label` (sampler name), `code` (response code), plus `run_id` and `step_id` attached by Prometheus.

## Appendix E. Console API

All paths are under the console's host. JSON bodies need `Content-Type: application/json`. Unless noted, a token (cookie or Bearer header) is required when `CONSOLE_TOKEN` is set.

| Method and path | Purpose |
|---|---|
| `GET /api/config` | (no token) `auth_required`, `grafana_url`, `dashboard_uid`, `large_run_instances` |
| `POST /api/login` | (no token) body `{token}`; sets the cookie |
| `GET /api/profiles` | list with summaries (phases, duration, peak, target, baseline, `needs_confirmation`, `confirm_reasons`) |
| `GET /api/profiles/<name>` | summary plus the YAML |
| `PUT /api/profiles/<name>` | body `{yaml, overwrite}`; validates first; 409 if it exists and not `overwrite` |
| `DELETE /api/profiles/<name>` | delete; 409 if that profile is running |
| `GET /api/profiles/<name>/precheck` | the checklist items |
| `POST /api/profiles/<name>/try` | the one-request test |
| `POST /api/validate` | body `{yaml}`; returns `{ok, message}` |
| `GET /api/runs` | recent runs (up to 50) |
| `POST /api/runs` | body `{profile, save_baseline, confirmed}`; 201 with the run; 428 with reasons if confirmation is needed; 409 if a run is active |
| `GET /api/runs/<id>` | the run, with `summary` once finished |
| `GET /api/runs/<id>/log?offset=<n>` | log text from a byte offset: `{text, offset}` |
| `GET /api/runs/<id>/live` | live numbers: `throughput`, `tps`, `error_pct`, `p95_ms`, `avg_ms`, `active_threads` |
| `PUT /api/runs/<id>/note` | body `{note}` (trimmed, up to 500 characters) |
| `POST /api/runs/<id>/stop` | stop a running run |
| `GET /builder.html` | the form builder |
| `GET /` and other static paths | the page, fonts |

Errors are `{"error": "..."}` with a status code (400, 401, 403, 404, 409, 413, 415, 428, 500).

## Appendix F. Profiles shipped (`profiles/`)

| File | What it shows |
|---|---|
| `rest-minimal.yaml` | 20 s run against the bundled REST target |
| `rest-endpoints.yaml` | a single 20 s phase, written with endpoints (the plan is generated from it) |
| `rest-two-step.yaml` | a two-phase example, 40 s in total |
| `rest-ramp-example.yaml` | a 4-step monotonic ramp |
| `rest-spike-example.yaml` | a 5-step spike pattern (needs `--yes`: a step exceeds 4 instances) |
| `rest-slow-detection.yaml` | slow-request detection |
| `rest-paced-regression.yaml` | pacing and a regression baseline |
| `rest-boomi-local.yaml` | the customers API through the local Atom (host machine, port 8078), API key from `.env` |
| `rest-backend-direct.yaml` | a control run: the same requests straight to the backend (an SSH tunnel on port 4005), skipping Boomi, to compare the two |
| `rest-backend-phased.yaml`, `rest-boomi-phased.yaml` | phased 10/20/40/80/20/10 requests per second |
| `rest-backend-phased-tps.yaml`, `rest-boomi-phased-tps.yaml` | phased 10/20/40/80/20/10 TPS, 40 s phases |
| `rest-boomi-phased-tps-20min.yaml` | 20-minute phased TPS profile |
| `rest-boomi-gateway.example.yaml` | template for a Boomi gateway (copy and fill in) |
| `jdbc-postgres-sample.yaml`, `jdbc-postgres-writes-sample.yaml` | database load, read-only and write |
| `rest-oauth-sample.yaml` | OAuth client credentials with 30 s tokens, 90 s run |
| `rest-cam-key-query-sample.yaml` | API key in the query string |
| `rest-cam-signature-sample.yaml` | API key plus signature |

Baselines committed in `baselines/`: `boomi-local-customers`, `boomi-phased`, `boomi-phased-tps`, `rest-paced`.

## Appendix G. Glossary

- **Profile:** a YAML file describing a test.
- **Step / phase:** one stage of a profile, with its own instances, threads, duration and optional target.
- **Instance:** one JMeter container.
- **Iteration:** one pass through all the requests in a profile's flow.
- **TPS:** iterations per second. **RPS / req/s:** individual requests per second. `rps = tps x requests per iteration`.
- **Baseline:** a saved known-good run used for regression comparison.
- **Stop rule:** a limit that halts a run.
- **Warm-up:** seconds at a step's start excluded from its reported numbers.
- **Staleness:** Prometheus marking a vanished target's series as ended, within seconds.
- **File-based service discovery:** Prometheus reading target files from a folder, picking up changes without a reload.
- **Cloud API Management (CAM) / Mashery:** Boomi's cloud API gateway.
- **Atom:** a Boomi runtime. **Listener:** a Boomi process that exposes an endpoint.
- **noVNC:** a browser-based remote desktop used by the authoring container.
- **JTL:** JMeter's raw results file.

## Appendix H. Command cheat sheet

```
./setup.sh                                  # set everything up (pulls images)
./console.sh                                # web console, http://localhost:8800
./ramp.sh profiles/rest-minimal.yaml        # run a profile
./ramp.sh --validate profiles/<file>        # check without running
./ramp.sh --try-request profiles/<file>     # send the first request once
./ramp.sh --save-baseline profiles/<file>   # record a known-good run
./ramp.sh --yes profiles/<file>             # skip the large-run confirmation
./run-test.sh rest/starter.jmx              # one starter plan, once
./smoke-test.sh                             # nine-check stack test
docker-compose up -d prometheus grafana rest-target graphql-target    # bring services back after a restart
docker-compose up -d oauth-target           # the OAuth and Cloud API Management samples
docker-compose up -d postgres-target        # the database sample
docker volume rm boomi-loadtest_grafana-data   # after changing the dashboard (stop grafana first)
.venv-ramp/bin/python -m pytest tests -q    # run the tests
```
