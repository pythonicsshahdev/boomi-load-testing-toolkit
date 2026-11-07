# Boomi Load Testing Toolkit

Load testing for APIs published through Boomi, built on JMeter, Prometheus and Grafana in Docker Compose. You describe a test as a short YAML profile (or build one in the included HTML builder), and the toolkit generates the JMeter plan, runs it in containers, and reports per-endpoint latency, throughput and errors. Results land in a Grafana dashboard that keeps history, so runs can be compared over time. A saved baseline turns a performance regression into a non-zero exit code for CI.

Nothing in the engine is Boomi-specific. It was built to test APIs fronted by Boomi API Management (API key, basic auth, bearer token or OAuth 2.0 client credentials), but it works against any HTTP API: REST, GraphQL or SOAP.

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
| `oauth-target` | Sample OAuth 2.0 API with 30 s tokens, started on demand | 8012 |

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

Pick a profile and see the load it will generate as a curve, with the duration, peak rate and target beside it. Before you run, a checklist shows whether Prometheus and Grafana are up and whether the credentials the profile needs are set in `.env` (names only, never values), and **Test one request** sends the profile's first request once, with its login, so a wrong key, path or host shows up before any load. Then Validate, Edit, Run, or Run and save as baseline.

While a run is going you see live throughput, average and slowest-5% latency, errors and threads, with the curve filling in as it progresses. When it finishes you get the results table, the baseline comparison in plain words, a link to that run in Grafana, **Export CSV**, and **Download report**, a single HTML page you can send to someone. Add a note to a run (for example what changed), and use History to filter by profile, status or note, or tick two runs and **Compare two runs** to see p95, errors and throughput side by side.

Other things the console does:
- **Duplicate** and **Delete** profiles, and a **Form builder** (`/builder.html`) that saves straight into the profile list.
- **Risk check.** A run that launches many instances, peaks above 100 TPS (or 200 requests per second), or sends load to a host that isn't local or one of the bundled samples asks for confirmation first.
- **Finished-run notification.** Set `CONSOLE_NOTIFY_WEBHOOK` in `.env` (or the environment) to a Slack or Microsoft Teams incoming-webhook URL, and the console posts one line when a run ends. It is off unless you set it.
- **Theme.** It follows your browser's light or dark setting. Add `?theme=light` to the address to force light.

It runs the same `ramp.py` underneath, so results are identical to the command line, and `./ramp.sh` keeps working for CI (`./ramp.sh --try-request <profile>` is the command-line version of Test one request).

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

## Authentication

Set `target.auth` in the profile. Secrets are never written in a profile: it names the `.env` variable that holds them, and the JMeter container reads them from the environment.

| `auth` | What it sends | `.env` variables |
|---|---|---|
| `none` | nothing | none |
| `basic` | HTTP Basic | `BOOMI_TEST_TARGET_BASIC_AUTH_USER`, `BOOMI_TEST_TARGET_BASIC_AUTH_PASS` |
| `api_key` | an API key header (`x-api-key` by default, change it with `api_key_header`) | `BOOMI_TEST_TARGET_API_KEY` |
| `bearer` | `Authorization: Bearer <token>` with a fixed token | `BOOMI_TEST_TARGET_BEARER_TOKEN` (or name another with `token_env`) |
| `oauth2_client_credentials` | a token fetched from your identity provider, refreshed before it expires | `BOOMI_TEST_OAUTH_CLIENT_ID`, `BOOMI_TEST_OAUTH_CLIENT_SECRET` |

### OAuth 2.0 client credentials

```yaml
target:
  protocol: https
  host: api.example.com
  port: 443
  auth: oauth2_client_credentials
  oauth:
    token_url: https://your-idp.example.com/oauth2/default/v1/token
    scope: loadtest.read          # optional
    audience: api://my-api        # optional (Auth0 and similar)
    client_auth: basic            # basic (client_secret_basic, default) or post (client_secret_post)
    refresh_skew_sec: 30          # fetch a new token this long before the old one expires
    client_id_env: MY_CLIENT_ID          # optional: use different .env variable names
    client_secret_env: MY_CLIENT_SECRET
```

- **Before any load starts**, `ramp.sh` fetches one token from inside a JMeter container. A wrong secret, a bad scope, a missing `.env` entry or an unreachable token URL stops the run with a message that says what to fix (exit code 1).
- **During the run**, each JMeter instance fetches its own token on its first request and refreshes it `refresh_skew_sec` before it expires, so a long run does not fail halfway with 401s. A failed refresh is retried every 5 seconds, not on every request.
- **Token requests are not counted** in the load-test metrics, so they don't skew latency or throughput.
- **Token URL reachability:** the URL is called from inside the container. For something on your own machine use `host.docker.internal`, never `localhost`.
- **Rate limits:** each parallel instance asks for its own token. Identity providers often rate-limit the token endpoint, so use a non-production tenant and keep token lifetimes at a few minutes or more.
- **Okta:** create an API Services app (client credentials grant), and use a custom authorization server (the built-in `default` one works) with a scope and an access-policy rule that allows the client credentials grant. The token URL is `https://<your-org>.okta.com/oauth2/<authServerId>/v1/token`. Okta's organization authorization server does not issue tokens for your own APIs.

To try it without an identity provider, start the bundled sample and run its profile. Its tokens last 30 seconds, so the 90 second run refreshes the token several times, and `/stats` shows how many tokens were issued and how many requests were rejected:

```bash
docker-compose up -d oauth-target
./ramp.sh profiles/rest-oauth-sample.yaml
curl -s localhost:8012/stats
```

Not covered yet: password and authorization-code grants, private-key JWT client authentication, mutual TLS, and API-key-as-query-parameter or signature schemes.

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
