# Technical Guide: JMeter + Prometheus + Grafana Performance Testing Stack for Boomi APIs

**Written:** 2026-10-15. This is a self-contained technical explainer of a performance-testing system built over several sessions — intended as source material you can paste into a conversation (Claude or otherwise) to generate summaries, diagrams, presentations, or documentation from. It assumes no prior context and no access to the underlying repository.

---

## 1. What this system is, and why it exists

This is a containerized load-testing platform for APIs published through Boomi — specifically REST, SOAP, and GraphQL endpoints reached through Boomi's API Management layer (basic auth or API key). It was built as the deliberately **lightweight** counterpart to a separate, heavier performance-testing system that integrates with real Boomi infrastructure, OpenTelemetry tracing, and OpenSearch. This project instead bundles its own Prometheus and Grafana, tests against bundled sample services or public demo endpoints, and has zero dependency on the heavier stack — making it fast to stand up, cheap to run, and safe to experiment with.

The system grew in three stages:

1. **A base stack**: single-request, single-instance JMeter test plans, with results flowing into Prometheus and visualized in Grafana.
2. **A distributed ramp/spike load generator**: a Python orchestrator that runs many JMeter instances in parallel, in configurable steps, to simulate realistic load patterns (gradual ramps, irregular spikes) rather than one-shot smoke tests.
3. **An expanded observability dashboard**: a 16-panel Grafana dashboard modeled on industry-standard load-test dashboard conventions (KPI cards, percentile trends, endpoint-level breakdowns).

---

## 2. Why JMeter, not a code-first tool like k6

The initial engine choice was explicitly re-evaluated mid-design. A full k6-based design had already been built and spec'd before a key constraint surfaced: the people who would actually author and maintain these tests are **low-code-friendly Boomi users, not developers**. k6 requires writing JavaScript for every test scenario; JMeter offers GUI-driven authoring (drag-and-drop samplers, extractors, assertions) that matches that audience far better. The trade-off was accepted explicitly: JMeter is a heavier JVM-based tool, needs a third-party plugin for Prometheus output (vs. k6's native support), but wins decisively on accessibility for the actual target users. k6 was "parked," not deleted, in case the audience assumption changes later.

Prometheus was chosen over an alternative like InfluxDB specifically because it's pull-based and Kubernetes-native (ServiceMonitor-style discovery) — relevant given the stack's intended eventual deployment path is Docker Hub images first, then a Kubernetes Helm chart.

---

## 3. Base stack architecture

Six Docker Compose services:

- **`jmeter`** — headless JMeter (runs via CLI `-n` flag) for actual load generation. A custom image bakes in JMeter 5.6.3 plus the third-party `jmeter-prometheus-plugin` (version 0.7.1), which adds a dedicated `PrometheusListener` test-plan element (distinct from JMeter's built-in "Backend Listener" concept) that exposes a Prometheus-scrapable `/metrics` HTTP endpoint directly from inside the running JMeter process.
- **`jmeter-gui`** — the same JMeter installation, but running under a virtual X display (Xvfb + fluxbox window manager) exposed via noVNC in a browser, so a user without a local JMeter install can still visually build and edit test plans. This container is for **authoring only** — actual load generation always goes through the headless service, matching JMeter's own documented best practice (running real load through a GUI skews results by consuming CPU on rendering).
- **`prometheus`** — scrapes the JMeter metrics endpoint on a 5-second interval, stores results.
- **`grafana`** — pre-provisioned with a dashboard and a Prometheus datasource baked into a custom image, so there's no manual setup step.
- **`rest-target`** and **`graphql-target`** — bundled sample services the test plans exercise by default (a FastAPI REST service and a FastAPI+Strawberry GraphQL service), so the whole stack is runnable standalone without needing a real Boomi environment. SOAP testing instead points at a public demo calculator service — not worth building a bespoke SOAP sample server for.

### Metric design

Three custom Prometheus collectors are defined inside each test plan's `PrometheusListener` element:
- `jmeter_requests_total` (counter) — every sample, labeled by sampler name (`label`) and HTTP status (`code`).
- `jmeter_response_time_ms` (histogram, buckets at 50/100/250/500/1000/2500/5000/10000ms) — per-request latency.
- `jmeter_failures_total` (counter) — specifically catches assertion failures that still return HTTP 200 (which a status-code-only view would miss).

A `RUN_ID` is injected as a `-J` CLI property and baked into each metric's label set, so Grafana can distinguish and compare multiple runs rather than blending them into one continuous series.

### The starter test plans

Each protocol's starter does a realistic two-step correlated flow (not just one isolated request): fetch a list, extract an ID from the response via a JSON/regex extractor, then fetch that specific record's detail — mirroring how a real integration test would chain calls. Each includes two response assertions (status code AND body content), so a broken extractor is caught immediately rather than silently producing a "successful" request against the wrong resource.

A deliberate quirk: each starter includes a 15-second pause element (JMeter's `TestAction` with action=PAUSE) immediately before the metrics listener. This exists *only* because these are single-shot, 1-2-request demo plans — without it, the JVM (and its embedded metrics HTTP server) would exit before Prometheus's 5-second scrape interval ever caught the data. It's explicitly documented as something to delete when cloning a starter for a real sustained test, since a real load test naturally stays alive far longer than one scrape interval.

---

## 4. The distributed ramp/spike load generator

The base stack could only run one JMeter instance at a time, firing a handful of requests — useful for validating correlation logic, not for simulating real load. The second major phase built a proper **distributed, multi-instance, multi-step load orchestrator**.

### Core insight: ramp and spike are the same mechanism

A "ramp" (gradually increasing load) and a "spike" (an irregular jump up and back down) are structurally identical — both are just a sequence of steps, each with its own instance count, thread count, and duration. The orchestrator doesn't need separate modes; it needs one flexible step schema:

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 30        # default for steps that don't override it
warmup_sec: 5                # excluded from each step's reported metrics
slow_request_threshold_ms: 2000
stop_rule:
  max_error_rate_pct: 2
  max_avg_response_ms: 3000
steps:
  - instances: 1
    threads: 5
  - instances: 4
    threads: 10
    duration_sec: 60         # optional per-step override
```

A monotonic ramp is a profile whose steps' instance counts only increase. A spike is a profile whose steps go up, down, and back up irregularly. Same engine, same YAML shape.

### Solving the "how do you scrape N simultaneous instances" problem

The base stack's Prometheus config pointed at one static target (`jmeter:9270`). That doesn't work once you're launching an arbitrary, varying number of parallel containers per step. The fix: **Prometheus's file-based service discovery.** Each launched instance gets:
- A deterministic container name (`jmeter-ramp-<run_id>-<step_id>-<instance_index>`).
- Its own small JSON file dropped into a watched directory, declaring its scrape target and attaching `run_id`/`step_id` as labels: `[{"targets": ["<container-name>:9270"], "labels": {"run_id": "...", "step_id": "..."}}]`.

Prometheus polls that directory every 5 seconds and picks up new/removed targets automatically — no reload, no restart. This is additive: the original static `jmeter` scrape job keeps working unchanged for the base stack's starters; the new file-based job runs alongside it for ramp instances. (A dedicated `jmeter-ramp` Docker Compose service, distinct from the base `jmeter` service, keeps the two scrape paths from ever colliding — see the pitfalls section below for why that distinction turned out to matter in practice.)

### The orchestrator (`ramp.py`)

Built in Python rather than bash, specifically because of how much structured-data handling is involved (YAML profile parsing, JSON HTTP responses, CSV result-file parsing, signal handling) — exactly the kind of thing that gets fragile fast in shell scripting. A thin `ramp.sh` wrapper just activates a virtualenv and hands off to the Python script.

Full feature set, each independently meaningful:

- **Parallel multi-instance launch per step** — for a step requesting 4 instances, 4 `docker-compose run` processes launch concurrently, each defensively removing any stale same-named leftover container first (a real hazard if a prior run was interrupted).
- **Prometheus-based stop-rule evaluation** — after each step, query actual error rate and average response time; if either exceeds a configured threshold, halt the remaining steps and report why, rather than blindly running a whole profile into a failure state.
- **A JMeter-native per-label, per-step summary report** — modeled directly on JMeter's own classic Aggregate Report format (Label / Samples / Average / Median / 90th / 95th / 99th percentile / Error % / Throughput), printed to the terminal after every run, broken out per sampler per step rather than blended together.
- **Slow-request detection** — independently scans the raw result files for any individual request exceeding a configurable latency threshold, surfacing genuine outliers that aggregate percentiles can hide.
- **Graceful interrupt handling** — a Ctrl+C mid-run stops the current step's containers and cleans up its Prometheus target files, rather than leaving orphaned containers running.
- **A safety confirmation gate** — launching more than a threshold number of simultaneous instances (default 4) requires explicit confirmation (or a `--yes` flag for unattended use), and fails fast with a clear message rather than hanging if there's no terminal to prompt.
- **Load-generator saturation awareness** — samples the host's own CPU usage against the launching containers partway through each step; if the load generator itself looks saturated, the report flags that step's numbers as potentially reflecting the generator's limits rather than the target's.
- **A `--validate` mode** — checks a profile for structural problems (missing fields, invalid types, a step with no resolvable duration) without launching anything, so a typo doesn't get discovered 20 minutes into a real run.
- **Grafana step annotations** — posts a start/end marker to Grafana for every step via its REST Annotations API, so a human looking at the dashboard afterward can see exactly when each load level began and ended.

A standalone, dependency-free HTML form (`ramp-profile-builder.html`) also exists for building a profile visually rather than hand-editing YAML — useful for the less code-comfortable half of the intended audience.

---

## 5. The Grafana dashboard (16 panels)

The dashboard evolved from a minimal 4-panel view (Request Rate, p95 Response Time, Error Rate, Active Threads) into a comprehensive one modeled on standard enterprise load-test dashboard conventions, organized in three tiers:

**KPI stat cards** (single-number, color-coded by threshold): Active Threads, Total Requests, Throughput (RPS — every individual request), Throughput (TPS — completed end-to-end flows), Error Rate (%).

**Percentile stats**: p90, p95, p99 response time as standalone numbers, plus a combined trend chart showing p50/p90/p95/p99 together over time — because an average hides exactly the kind of latency spike that matters most.

**Trend charts**: Request Rate over time, Error Rate over time, Active Threads over time, and a three-way **Connect Time vs. Latency vs. Response Time** breakdown — separating TCP connection setup, server processing time (time-to-first-byte), and total elapsed time including the full response transfer, so a latency problem can be attributed to the right layer.

**Endpoint-level breakdown**: a summary table (per-endpoint samples/average/p95/error-rate), a "Top Slowest Endpoints" ranked bar gauge, and an "Error Tracking by Code" view grouping request rate by endpoint *and* HTTP status code together, so a rising 4xx/5xx line can be traced to a specific route.

A `run_id` and `step_id` dropdown filter both let a viewer isolate one specific test run, and optionally one specific step within a multi-step ramp/spike profile.

---

## 6. Real technical pitfalls encountered and fixed (the part most worth keeping)

These aren't hypothetical — each was discovered, reproduced, and fixed during actual verification against a live stack, not assumed from documentation.

**A silent DNS trap in Docker Compose.** Running a one-off container via `docker-compose run jmeter ...` without an extra `--use-aliases` flag means Docker's embedded DNS never registers that container under its service name — so Prometheus can never resolve it as a scrape target. The test still runs and reports success; it just silently exports zero metrics, with no error anywhere. This was, by the project's own account, the single most expensive thing to debug while building the base stack.

**A non-existent JMeter function that fails silently.** `${__env(VAR_NAME)}` looks like it should read an OS environment variable into a JMeter property — it does not exist as a real function in a stock JMeter install, and resolves to a literal placeholder string with *zero warning*. The correct, verified pattern is `${__groovy(System.getenv("VAR_NAME"))}`. This was caught in a final code review and had been silently breaking every credential reference in the project until then.

**Prometheus staleness timing is tighter than it looks.** A scrape target that disappears gets marked "stale" by Prometheus within roughly 3-4 seconds of its last successful scrape — not the full 5-minute lookback window a casual reading of Prometheus's docs might suggest. Querying an instant value even slightly past that mark returns nothing, which can look exactly like "the data wasn't collected" when it actually was. This bit the project twice: once in basic run-reporting (fixed by querying at an explicit historical timestamp instead of "now"), and once in the load orchestrator's own summary report, where a step's entire row could silently vanish from the output with zero warning because the query instant landed right on that edge. Fixed with a small time buffer subtracted before querying.

**An unaggregated multi-instance query looks like a real number but isn't one.** A Prometheus query scoped to a specific run but not explicitly summed across instances returns one series per instance — picking the first one arbitrarily rather than a total. In a 4-instance load step, this made the summary report show *fewer* total requests than a 1-instance step, inverting the apparent result exactly backwards from reality (looking like load collapsing as capacity increased). The fix is a one-word `sum()` wrapper, but the bug is easy to introduce and easy to miss, since the query still returns *a* plausible-looking number.

**A Grafana gauge can look broken even when the underlying metric is correct.** An "Active Threads" panel querying a per-instance gauge metric (with no way to group it by test run) rendered as scattered, identically-labeled, disconnected dots rather than a usable line — because each short-lived load-generator container reports its own thread count independently, and multiple containers running simultaneously (or sequentially across a session) all show up as separate series that happen to share the same legend text. The fix was summing across all currently-reporting instances into one combined total, which is what the panel was conceptually supposed to show all along.

**A JMeter Transaction Controller's "generate parent sample" option is a one-way door.** The textbook-correct way to get a true transactions-per-second metric for a multi-request flow is wrapping the chained requests in a Transaction Controller configured to generate an aggregate "parent" sample. In practice, with this project's specific Prometheus plugin, enabling that option caused the *individual* request-level metrics to stop being exposed entirely — only the aggregate transaction showed up, breaking every panel that needed per-endpoint detail. A follow-up attempt to work around this (adding a second, more narrowly scoped metrics listener) didn't crash, but silently corrupted the actual test's execution throughput by roughly three orders of magnitude. The practical fallback: derive an approximate transactions-per-second number by filtering the existing per-request metric down to just the first request in each flow (which fires exactly once per completed iteration) — an exact count with zero structural risk, at the cost of its reported latency reflecting only that first hop rather than the full chain.

**Multi-layer escaping in query-embedded regular expressions is a real trap.** Matching a literal special character (like a `+` in an endpoint name) through a regular expression that's itself embedded inside a query language's string literal, which is itself stored inside a JSON configuration file, requires escaping that character correctly at *three* separate layers simultaneously. Getting the escaping wrong at one layer doesn't necessarily produce an error — it can produce a query that's syntactically valid but silently matches nothing, or the wrong thing. Verifying this kind of query against live data (not just confirming the configuration file parses) is the only reliable way to catch it.

**Persistent state can mask configuration changes.** The dashboarding tool here stores its own internal database in a Docker volume that survives container restarts. Rebuilding the underlying image with a changed dashboard definition and recreating the container does *nothing* visible, because the tool's own database already has the old version imported and keeps serving it. The fix requires explicitly wiping that persistent volume, not just rebuilding and restarting — an easy step to miss since nothing about a normal rebuild-and-restart cycle suggests it's necessary.

---

## 7. Current status

All of the above is implemented, individually verified against a live running stack (not just unit-tested in isolation), and reviewed by a fresh, independent pass that specifically looked for correctness issues, edge cases, and deviations from the original design — with findings categorized by severity and every finding either fixed-and-reverified or explicitly, transparently deferred with reasoning. Two further pieces of work are scoped but not yet started: integrating against a real Boomi-published API target (rather than the bundled sample services), and wiring the whole thing into a CI/CD pipeline for automated, scheduled, or pipeline-triggered load testing.
