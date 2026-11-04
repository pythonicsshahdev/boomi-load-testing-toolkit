# Usage Guide: JMeter + Prometheus + Grafana Performance Testing Stack

**Written:** 2026-10-15. A step-by-step, copy-pasteable walkthrough — covers local JMeter setup, Docker-based JMeter setup, first-time stack setup, running single tests, running distributed ramp/spike load tests, and viewing results. Companion to `technical-guide-2026-10-15.md` (which explains the *why*; this explains the *how*). All commands assume you're in the `POC_DEVELOPMENT/` directory of this repo unless noted otherwise.

---

## Part 1 — Setting up JMeter locally

This is the path for someone who wants to author or edit `.jmx` test plans using a native desktop JMeter GUI, rather than the browser-based option in Part 2. It's optional — you can skip straight to Part 3 if you only intend to use the Docker-based GUI or just run existing test plans headlessly.

### 1.1 Install JMeter

On macOS with Homebrew:

```bash
brew install jmeter
```

Confirm it installed and find its home directory (you'll need this path in the next step):

```bash
jmeter --version
JMETER_HOME="$(brew --prefix jmeter)/libexec"
echo "$JMETER_HOME"
```

### 1.2 Install the required Prometheus plugin

**This step is not optional.** Every `.jmx` file in this repo includes a `PrometheusListener` element from a third-party plugin. Without it, a stock local JMeter GUI can't even *open* these files — it fails immediately with `CannotResolveClassException`, not just on run.

Install the exact same version this project's Docker image uses (confirmed from `jmeter/Dockerfile`):

```bash
curl -fsSL "https://github.com/johrstrom/jmeter-prometheus-plugin/releases/download/0.7.1/jmeter-prometheus-plugin-0.7.1.jar" \
  -o "$JMETER_HOME/lib/ext/jmeter-prometheus-plugin-0.7.1.jar"
```

### 1.3 (Optional) Install JMeter Studio for a modernized theme

Purely cosmetic — matches the look of the Docker-based GUI described in Part 2. Also confirmed from this project's own Dockerfile:

```bash
curl -fsSL "https://github.com/QAInsights/jmeter-studio/releases/download/v1.0.2/jmeter-studio-oss-1.0.2.jar" \
  -o "$JMETER_HOME/lib/ext/jmeter-studio-oss-1.0.2.jar"
```

After installing, launch JMeter, then go to **Options → Themes** to switch to it.

### 1.4 Launch and verify

```bash
jmeter
```

In the GUI, open any `.jmx` file from this repo's `test-plans/` directory (e.g. `test-plans/rest/starter.jmx`). If the plugin installed correctly, it opens cleanly with no error dialog. One known cosmetic quirk: clicking directly on the "Prometheus Listener" tree node specifically may show a dismissible `guicomp is null` error — this is a known quirk in the plugin's own GUI registration, has no effect on actually running tests, and the rest of the tree remains fully usable. If you're building a new test plan, copy the Prometheus Listener element from an existing starter rather than configuring one from scratch through its GUI panel.

**Note:** this local install is for *authoring and editing* test plans only. Don't use the local GUI to run real load tests — JMeter's own documented best practice is that GUI mode consumes CPU rendering results, which skews the numbers. Real test execution should always go through headless mode (`jmeter -n -t ...`), either locally or via the Docker services in Part 2/3.

One thing you do *not* need to configure locally: the Docker image's `jmeter.properties.append` sets `prometheus.ip=0.0.0.0` so the metrics endpoint is reachable from other containers over the Docker network. A local install doesn't need this — nothing else needs to reach into your local machine to scrape it, and the default binding works fine for local authoring/debugging.

---

## Part 2 — Setting up JMeter via Docker (browser-based GUI, no local install)

If you'd rather not install JMeter locally at all, this project ships a containerized JMeter GUI you access through a browser via noVNC — same plugin, same theme, zero local install.

### 2.1 Start it

```bash
docker-compose up -d jmeter-gui
```

### 2.2 Open it

Go to **http://localhost:6080/vnc.html** in a browser and click **Connect** (no password). JMeter's GUI launches automatically inside the container's virtual desktop as soon as the container starts — you don't need to manually open it from a terminal inside the session.

### 2.3 Edit test plans

The container bind-mounts the same `test-plans/` directory your local filesystem uses, so any `.jmx` file you open/save inside this noVNC session is immediately visible outside it too (and vice versa — if you used Part 1's local GUI, both paths share the same files).

### 2.4 Stop it when you're done

```bash
docker-compose stop jmeter-gui
```

**Same rule as Part 1 applies: this container is for authoring only.** Running a real load test inside this GUI session would skew results the same way a local GUI would.

---

## Part 3 — First-time stack setup

This only needs doing once per clone.

### 3.1 Clone and configure credentials

```bash
cp .env.example .env
```

Then open `.env` and fill in the `BOOMI_TEST_TARGET_*` values for whatever real API you intend to test (these aren't needed to run the bundled sample services, only for pointing the stack at a real Boomi-published API). These get read into test plans via `${__groovy(System.getenv("VAR_NAME"))}` in each plan's Authorization Manager / Header Manager — **not** `${__env(...)}`, which looks like it should work but silently resolves to the literal placeholder text with zero warning if used by mistake.

### 3.2 Build the images

```bash
docker-compose build
```

This builds the custom JMeter image (JMeter 5.6.3 + the Prometheus plugin), the Docker-based GUI image, the Grafana image (dashboards + datasource pre-provisioned), and the GraphQL sample service image.

### 3.3 Bring up the long-running services

```bash
docker-compose up -d prometheus grafana rest-target graphql-target
```

(`jmeter`/`jmeter-ramp` are invoked on-demand per test run, not left running continuously — see Part 4.)

### 3.4 Verify everything is healthy

```bash
./smoke-test.sh
```

This checks target reachability, Grafana/Prometheus health, and — critically — that metrics actually flow end-to-end from a real JMeter instance into Prometheus, not just that services respond to a ping.

---

## Part 4 — Running a single test

```bash
./run-test.sh rest/starter.jmx
```

This generates a unique run identifier, runs the test plan headlessly, and prints that identifier along with a direct link to view it in Grafana. Try the other protocols too:

```bash
./run-test.sh graphql/starter.jmx
./run-test.sh soap/smoke-test.jmx   # hits a public demo, needs internet access
```

Run the same command twice and you'll get two different run identifiers — useful for confirming Grafana can tell separate runs apart (see Part 6).

---

## Part 5 — Running a distributed ramp or spike load test

This is for simulating realistic load patterns — many parallel JMeter instances, stepped up (or up-and-down) over time — rather than a single quick smoke test.

### 5.1 One-time setup

```bash
python3 -m venv .venv-ramp          # Python 3.9 or newer
source .venv-ramp/bin/activate
pip install -r requirements-ramp.txt
```

### 5.2 Validate a profile before running it

```bash
./ramp.sh --validate ramp-profiles/rest-ramp-example.yaml
```

This checks the profile's structure (required fields, valid types, every step has a resolvable duration) without launching anything — catching a typo before it costs you a real test run.

### 5.3 Run it

```bash
./ramp.sh ramp-profiles/rest-ramp-example.yaml
```

This particular example profile runs 4 steps, each increasing in instance/thread count. Watch the terminal — you'll see each step announce itself as it launches, a final per-step OK/FAILED summary, then a full JMeter-native Aggregate Report broken down by sampler label per step, and (if the profile sets a `slow_request_threshold_ms`) a table of individually slow requests.

Try the spike pattern too — an irregular up-down-up load shape instead of a steady increase:

```bash
./ramp.sh --yes ramp-profiles/rest-spike-example.yaml
```

(The `--yes` flag is needed here specifically because one of this profile's steps launches more than 4 simultaneous instances, which triggers a safety confirmation prompt by default — `--yes` skips that prompt for unattended use. Without it, and without a terminal attached to answer the prompt, the run fails fast with a clear message rather than hanging.)

### 5.4 Writing your own profile

Either hand-edit a YAML file following the schema shown in the examples above, or open `ramp-profile-builder.html` directly in a browser (no server needed — it's a fully standalone page) and use the form to generate one.

#### Defining endpoints in the profile (no JMeter GUI needed)

Add a `target:` and a `requests:` block (the builder page has a "Target & Endpoints" section that writes them). When both are present, `ramp.py` generates a JMeter plan for the run at `test-plans/generated/<run_id>.jmx`, using the profile's `test_plan` as the template (thread group and Prometheus listener are kept; defaults, auth and samplers are replaced). Without these blocks, `test_plan` runs unchanged.

```yaml
target:
  protocol: http          # http | https
  host: rest-target       # compose service name, host.docker.internal, or a public hostname
  port: 4005              # optional (omit for 80/443)
  auth: none              # none | basic | api_key  (credentials come from .env)
requests:
  - name: "List customers"        # becomes the Grafana label; must be unique
    method: GET                   # GET | POST | PUT | PATCH | DELETE
    path: "/customers?limit=5"
    extract: "id=$[0].customer_id"   # optional: var=jsonpath, usable as ${id} in later paths
  - name: "Get customer"
    path: "/customers/${id}"
```

Optional per request: `body` (raw JSON string; adds a JSON Content-Type header). Optional on target: `api_key_header` (default `x-api-key`). See `ramp-profiles/rest-endpoints.yaml` for a working example.

#### Pacing: think time and a target request rate

Both apply to generated plans only (profiles with `target`/`requests`).

```yaml
think_time_ms: 200          # pause before each request (a request can override: think_time_ms on the request)
steps:
  - instances: 2
    threads: 5
    rps: 40                 # total requests/sec for the step, split evenly across its instances
```

**TPS instead of req/s.** Enterprises usually size load in transactions per second, so a step can say `tps: 40` instead of `rps`. One transaction is one pass through the flow (every request in `requests:`), so `tps: 40` with 2 requests per iteration is 80 req/s. `ramp.py` does that conversion for you and prints both numbers at launch. Set either `tps` or `rps` on a step, not both. In the summary table each request's `Thrpt` equals the achieved TPS, and the Grafana "Throughput (TPS)" panel shows the same thing. See `ramp-profiles/rest-boomi-phased-tps.yaml`.

`rps` and `tps` are **caps**, not guarantees: they can only throttle. If you set it higher than your threads and think time allow (roughly `threads x instances / (response time + think time)` per request), the real rate lands below it. Add threads if the summary's `Thrpt` falls short.

#### Planning a phased run to a total duration

To make a phased run last a set time, divide the total by the number of phases and set that as `step_duration_sec`. Every step then runs for that long, so the run lasts `phases x step_duration_sec`. For 20 minutes across six phases (10 > 20 > 40 > 80 > 40 > 20 TPS), that is 1200 s / 6 = **200 s per phase**:

```yaml
step_duration_sec: 200     # 6 phases x 200 s = 1200 s = 20 minutes
warmup_sec: 15             # excluded from each step's reported metrics
think_time_ms: 200
steps:
  - { instances: 1, threads: 8,  tps: 10 }
  - { instances: 1, threads: 14, tps: 20 }
  - { instances: 1, threads: 26, tps: 40 }
  - { instances: 1, threads: 50, tps: 80 }
  - { instances: 1, threads: 26, tps: 40 }
  - { instances: 1, threads: 14, tps: 20 }
```

The full profile is `ramp-profiles/rest-boomi-phased-tps-20min.yaml`. Notes:

- **Uneven phases.** Add `duration_sec` to a step to override `step_duration_sec` for that step only, for example `duration_sec: 400` to hold the 80 TPS peak longer. The total is then the sum of the step durations.
- **Threads per phase.** Size threads from the target: each thread sustains about `1 / (response time + think time)` iterations per second per request, so with a 200 ms think time and fast responses roughly 2.3 TPS per thread. If the summary's `Thrpt` comes in below the target, add threads (see the caps note above).
- **Warmup counts toward the step time.** `warmup_sec` is excluded from the reported metrics but is part of `step_duration_sec`, so the run time does not change.
- **Name a new baseline.** Give a longer or reshaped profile its own `regression.baseline` name (here `boomi-phased-tps-20min`) so it is never compared against a profile with different phases.
- **Check before a long run.** `./ramp.sh --validate <profile>.yaml` catches mistakes in seconds, which matters for a 20-minute run against a shared Atom. `Ctrl+C` stops the running containers and exits with code 130, but prints no summary report; the steps that already finished stay visible in Grafana under the same `run_id`.
- **Where to watch it.** Grafana is on **http://localhost:3004** (`admin` / `admin`). The run shows up under its `run_id`, with a start and end annotation for every phase.

#### Regression gate (baseline comparison)

```yaml
regression:
  baseline: rest-paced              # stored in baselines/rest-paced.json
  max_p95_increase_pct: 30          # default 25
  max_error_rate_increase_pct: 1    # percentage points, default 1
  min_p95_delta_ms: 15              # ignore p95 moves smaller than this, default 10
```

```bash
./ramp.sh --save-baseline ramp-profiles/rest-paced-regression.yaml   # record a known-good run
./ramp.sh ramp-profiles/rest-paced-regression.yaml                   # later runs are compared against it
```

After the summary, each step|label is compared with the baseline. A p95 increase must exceed both the percentage and the absolute-ms limits (3ms to 6ms is +100% but noise). A row with no samples counts as a failure (it can't prove there was no regression). Labels new to or missing from the baseline are reported but don't fail. Exit codes: `2` stop rule, `3` failed instance, `4` regression. A baseline is not saved from a run that hit a stop rule or had failed instances. `baselines/*.json` are meant to be committed. p95 comes from Prometheus histogram buckets. The `ramp.jmx` plans use fine buckets from 1 ms up (1/2/5/10/15/20/25/30/40/50/75/100/150/250... ms), but a baseline saved before 2026-10-25 used coarse 50/100/250... ms buckets, so re-save it with `--save-baseline` before trusting p95 comparisons, and keep thresholds at 20% or more.

#### Testing a real Boomi-published API

Copy `ramp-profiles/rest-boomi-gateway.example.yaml`, fill in the placeholders, put credentials in `.env`, and start with the low settings in the file. The host must be reachable from inside the JMeter container (a public or VPN hostname, or `host.docker.internal`; not `localhost`).

#### Load testing a database (JDBC)

The same profiles can drive SQL queries instead of HTTP requests. Set `target.protocol: jdbc` and write each entry in `requests:` as a query. PostgreSQL is supported today. Everything else works the same: phases, `tps`/`rps` pacing, think time, stop rules, baselines, the Grafana dashboard and the console. Each query's `name` becomes its label in the results, and one iteration runs every query once, so `tps: 20` with three queries is 60 queries/sec.

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 30
warmup_sec: 5
target:
  protocol: jdbc
  driver: postgresql
  host: postgres-target        # a compose service name, or host.docker.internal for the Mac itself
  port: 5432
  database: loadtest
requests:
- name: Customer by id
  sql: SELECT id, name, email, city FROM customers WHERE id = ?
  params:
  - { value: "${__Random(1,100000)}", type: INTEGER }   # a different row each time
steps:
- { instances: 1, threads: 10, tps: 100 }
```

- **Credentials** come from `BOOMI_TEST_DB_USER` and `BOOMI_TEST_DB_PASSWORD` in `.env`, never from the profile. If they aren't set, the bundled sample database's login (`loadtest`/`loadtest`) is used.
- **Parameters** replace the `?` placeholders in order. The number of `?` must match the number of `params`. Types are `INTEGER`, `BIGINT`, `SMALLINT`, `VARCHAR`, `DECIMAL`, `DOUBLE`, `BOOLEAN`, `TIMESTAMP`. A value can be a JMeter function such as `${__Random(1,100)}` so queries don't all hit the same row, which would only measure the database's cache.
- **Writes are off by default.** A query with `query_type: update` (INSERT, UPDATE, DELETE) is refused unless the target says `allow_writes: true`. Point write tests at a scratch table or a disposable database. `ramp-profiles/jdbc-postgres-writes-sample.yaml` shows one.
- **Connections.** Each thread keeps its own connection, so threads don't wait on a shared pool and the numbers reflect the database. Results are counted, not stored, so the load generator's memory isn't the limit.
- **Try it:** `docker-compose up -d postgres-target`, then `./ramp.sh ramp-profiles/jdbc-postgres-sample.yaml`. The sample has 100,000 customers and 300,000 orders. It listens on `localhost:5434` if you want to look at it yourself.
- **Your own database:** set the two variables in `.env`, point `host`/`port`/`database` at it (from inside the JMeter container a service on your Mac is `host.docker.internal`, not `localhost`), and use a read-only login for read tests. Start with low thread counts: this is real load on a real database.
- **Another database** needs its JDBC driver jar added in `jmeter/Dockerfile` and an entry in `JDBC_DRIVERS` in `ramp.py`. Snowflake and others are not set up yet.
- **The profile builder page (`ramp-profile-builder.html`) only builds HTTP profiles.** For a database profile, edit the YAML in the console or in a text editor.
- **The JMeter GUI image** (`jmeter-gui`) needs a rebuild (`docker-compose build jmeter-gui`) to open JDBC plans, because it is built on top of the JMeter image that carries the driver.

#### When a run looks empty or wrong

- **Short steps.** Prometheus is scraped every few seconds, so a step shorter than about 20 seconds (after warmup) can produce an incomplete report. `ramp.py` warns at the start of such a run. Use `duration_sec` of 30 or more for anything you rely on.
- **Raw-results check.** After the summary, `ramp.py` reads JMeter's own results files and lists the most common failure messages per step. That is how a bad password, a refused connection or a wall of 401s shows up as plain text, even when the Prometheus-based table is empty.
- **Exit code 5.** If a step recorded no measurable samples, the run ends with exit code 5 and a warning instead of passing quietly. The console shows it as "No usable data".

---

## Part 6 — Viewing results in Grafana

Open **http://localhost:3004** (login: `admin` / `admin`), and open the dashboard named **"Boomi Performance Testing — JMeter Overview."**

At the top, use the `run_id` dropdown to select a specific test run (or `step_id` too, if you ran a multi-step ramp/spike profile and want to isolate one step). You should see:

- The KPI row at the top (Active Threads, Total Requests, Throughput in both RPS and TPS, Error Rate) update to reflect your selection.
- The percentile stats (p90/p95/p99) and the combined percentile trend chart.
- Request Rate, Error Rate, and Active Threads over time.
- A Connect Time / Latency / Response Time breakdown — only populated for runs using the `ramp.jmx` test plans, since that's where those specific metrics are collected.
- An Endpoint-Level Summary table, a Top Slowest Endpoints bar gauge, and an Error Tracking by Code panel — give these a few seconds after a run finishes before expecting data, since they use point-in-time queries that can briefly show nothing immediately after a run completes.
- If you ran a ramp/spike profile, vertical annotation markers on the time-series panels showing exactly when each step started and ended.

---

## Part 7 — The web console (run tests from a browser)

The console is a small web page that runs `ramp.py` for you. Use it when you would rather click than type commands. Everything it does can still be done with `./ramp.sh`, and both produce the same results.

### 7.1 Start it

```bash
./console.sh
```

Then open **http://localhost:8800**. The stack from Part 3 must be up (`docker-compose up -d prometheus grafana rest-target graphql-target`), because the console starts the same JMeter containers a command-line run does. Stop the console with `Ctrl+C`. A run that is in progress keeps going without it, and the console picks it back up when you restart it.

### 7.2 What you can do

- **Pick a profile** from the list on the left. The card shows the total time, number of phases, peak rate, target and the phase shape (bar height is the target rate, width is the duration).
- **Validate** checks the profile with `ramp.py`'s own validator, so the console and the command line never disagree.
- **Run** starts it. **Run and save as baseline** starts it and, if the run is clean, records it as the baseline named in the profile's `regression:` block.
- **Edit YAML** opens the profile in a text editor. Saving validates first, never overwrites without asking, and writes into `ramp-profiles/`. **+ New profile** starts from a template. Credentials never go in a profile; they stay in `.env`.
- **Watch a run.** Progress, the current phase and the log update every second or two. **Stop run** ends it early: the JMeter containers are removed and the earlier phases stay visible in Grafana.
- **Read the outcome.** Results show per-phase, per-endpoint samples, average, median, p90/p95/p99, errors and throughput. The Baseline check tab shows the comparison, and a plain-words banner says whether the run passed, hit a stop rule, had an instance fail, or regressed. **View in Grafana** opens the dashboard on that run's `run_id`.
- **History** lists earlier runs. Click one to reopen its results and log.

Only one run can be active at a time. A profile that launches more than 4 simultaneous JMeter instances asks for confirmation first, the same gate `ramp.sh` has.

A profile can be opened directly with a link such as `http://localhost:8800/#profile=rest-boomi-phased-tps-20min`.

### 7.3 Running the console on a VM (EC2 or similar)

By default the console only listens on `127.0.0.1`, and it refuses any other address unless an access token is set, because it can start load against real systems.

**Simplest and safest: keep it on localhost and tunnel to it.** On the VM run `./console.sh`, and on your laptop run:

```bash
ssh -L 8800:localhost:8800 -L 3004:localhost:3004 <user>@<vm>
```

Then browse to http://localhost:8800 as usual. No ports are opened to the network, and the Grafana links keep working.

**To let a team reach it directly**, set a token and bind to the VM's address:

```bash
export CONSOLE_TOKEN=$(openssl rand -hex 24)     # at least 16 characters; or put CONSOLE_TOKEN=... in .env
./console.sh --host 0.0.0.0 --allow-host loadtest.example.com
```

Everyone signs in with that token. Details to know:

- `--allow-host` lists the DNS name people use to reach the VM. Requests addressed to any other name are refused on a localhost bind, which blocks DNS-rebinding tricks.
- **There is no TLS in the console.** Put it behind an HTTPS reverse proxy or load balancer, or use the SSH tunnel, before sending the token over a network you don't trust. The sign-in cookie is `HttpOnly` and `SameSite=Strict`; it is only marked `Secure` if your proxy adds that.
- It is one shared token, not per-user accounts, and one run at a time for the whole VM.
- Don't open ports 8800, 3004 or 9090 to the internet. Grafana still has its default `admin`/`admin` login, so change that password on any shared machine.
- `--grafana-url https://grafana.example.com` sets the link target if Grafana isn't reachable on the same host at port 3004.
- Run history is stored under `console/runs/` on the VM (gitignored).

**Linux host requirements.** Docker Engine, Python 3.9 or newer, and either the `docker-compose` binary or the Compose v2 plugin (`ramp.py` uses whichever it finds). `host.docker.internal` is mapped to the host for the JMeter containers, so profiles that target a service on the VM itself work the same way as on a Mac. Result files written into `test-plans/` by the containers are owned by root on Linux; see the Linux note in `POC_DEVELOPMENT/README.md`.

> Not yet verified on a real Linux VM. The console and `ramp.py` were built and tested on macOS; the Linux-specific changes (CPU count lookup, Compose command, `host.docker.internal` mapping) are covered by unit tests and a macOS regression run, but a first run on EC2 may turn up something that is only visible there.

---

## Quick reference: all the entry points in one place

| What you want to do | Command |
|---|---|
| Author/edit test plans (local GUI) | `jmeter` (after Part 1 setup) |
| Author/edit test plans (browser GUI) | `docker-compose up -d jmeter-gui`, then open `localhost:6080/vnc.html` |
| Run one quick test | `./run-test.sh <protocol>/<file>.jmx` |
| Check the whole stack is healthy | `./smoke-test.sh` |
| Validate a ramp/spike profile | `./ramp.sh --validate <profile>.yaml` |
| Run a ramp/spike load test | `./ramp.sh [--yes] <profile>.yaml` |
| Run a phased test of a set length | `step_duration_sec` = total / phases (see "Planning a phased run to a total duration") |
| Run and watch tests in a browser | `./console.sh`, then open `http://localhost:8800` |
| Build a profile visually | open `ramp-profile-builder.html` in any browser |
| View results | `http://localhost:3004` |
