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
python3.14 -m venv .venv-ramp
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

## Quick reference: all the entry points in one place

| What you want to do | Command |
|---|---|
| Author/edit test plans (local GUI) | `jmeter` (after Part 1 setup) |
| Author/edit test plans (browser GUI) | `docker-compose up -d jmeter-gui`, then open `localhost:6080/vnc.html` |
| Run one quick test | `./run-test.sh <protocol>/<file>.jmx` |
| Check the whole stack is healthy | `./smoke-test.sh` |
| Validate a ramp/spike profile | `./ramp.sh --validate <profile>.yaml` |
| Run a ramp/spike load test | `./ramp.sh [--yes] <profile>.yaml` |
| Build a profile visually | open `ramp-profile-builder.html` in any browser |
| View results | `http://localhost:3004` |
