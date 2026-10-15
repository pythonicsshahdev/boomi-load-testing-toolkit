# Handoff: Lightweight JMeter Performance POC → OTel/`perf/` Session

**Written:** 2026-10-15, by the Claude session working in `BOOMI_Performance_Testing_POC`.
**Audience:** a Claude Code session working in the OTel Fanout Platform repo (`vector_simplified`), specifically the heavier `perf/` performance-testing subsystem there.
**Why this exists:** these two projects both tackle "load-test Boomi-published APIs," deliberately split apart so each has its own simpler blast radius (see "Why two projects" below). This document summarizes what was built, broken, fixed, and learned in the lightweight one, with an eye toward what's actually transferable to the heavier one — not a merge proposal, a lessons-and-capabilities handoff.

---

## Why two projects exist (read this first, so nothing here gets mistaken for a merge request)

- **This project** (`BOOMI_Performance_Testing_POC`, this repo) is the deliberately lightweight counterpart: bundled Prometheus + Grafana (not reused from an existing stack), no OpenSearch/OTel/Data Hub/Agent Studio dependency, generic REST+GraphQL+SOAP protocol coverage against bundled sample services or public demos — not real Boomi components.
- **Your project** (`perf/` inside `vector_simplified`) is the ambitious one: real Boomi listeners under test, an API Management gateway path, OpenSearch/OTel traces, Data Hub snapshot storage, an Agent Studio "Boomi Performance Analyst" agent. It already has its own `docker-compose.yml`, `run-test.sh`, and `ramp.sh` — built independently, around the same original idea, but with real infrastructure behind it.
- The split was intentional (confirmed explicitly by the user during this project's early sessions) specifically so this lightweight project wouldn't inherit OpenSearch/OTel/Data Hub complexity. **Nothing in this document is a proposal to merge the two** — it's a report of what got built, what broke, and what got fixed over here, in case any of it is useful over there.

If your session wants the full origin story: `/Users/shahabdullahmac/Downloads/IMPLEMENTATION_GUIDE.html` is the full writeup of your own project as of 2026-10-13 (referenced from this project's memory, not re-verified by this handoff).

---

## What exists in this repo right now

Everything below is pushed to `https://github.com/pythonicsshahdev/BOOMI_Performance_Testing_POC` (private), on `main`, as of commit `14b53ca`. All paths below are relative to this repo's `POC_DEVELOPMENT/` subfolder.

### 1. Base stack (11 tasks, shipped first)
- `docker-compose.yml`: JMeter (headless `jmeter` service + browser-GUI `jmeter-gui` noVNC service), Prometheus, Grafana, a REST sample target (reused existing image), a GraphQL sample target (new, FastAPI+Strawberry).
- `jmeter/Dockerfile`: JMeter 5.6.3 + `jmeter-prometheus-plugin` 0.7.1 baked in.
- `test-plans/{rest,graphql}/starter.jmx`, `test-plans/soap/smoke-test.jmx`: single-shot demo test plans (2 requests, list→detail correlation, a 15s `TestAction` pause to keep the JVM alive long enough for one Prometheus scrape).
- `run-test.sh` / `smoke-test.sh`: the operator-facing entry points for a single run and a full stack health check.
- Full spec/plan: `docs/superpowers/specs/2026-10-12-jmeter-boomi-performance-stack-design.md` / `docs/superpowers/plans/2026-10-12-jmeter-boomi-performance-stack.md`.

### 2. Distributed ramp/spike load generation (16 tasks, shipped second)
- `ramp.py` (+ thin `ramp.sh` wrapper): a Python orchestrator — YAML profile → parallel multi-instance JMeter launches per step → Prometheus-based stop-rule evaluation → JMeter-native per-label summary report → slow-request detection from `.jtl` files → graceful SIGINT/SIGTERM cleanup → a confirmation gate above an instance-count threshold → a load-generator CPU-saturation check → `--validate` mode → Grafana step-start/step-end annotations.
- `test-plans/{rest,graphql,soap}/ramp.jmx`: sustained-load siblings of the starters (loop for a configured duration instead of firing once).
- `prometheus/prometheus.yml`: a **second** scrape job (`jmeter-ramp`, file-based service discovery) added alongside the original static `jmeter` job — needed because a single static target can't discover N simultaneous ramp instances.
- `docker-compose.yml`: a dedicated `jmeter-ramp` compose service (separate from the base `jmeter` service) — see Lesson 3 below for why this exists.
- `ramp-profiles/*.yaml`: example profiles (a monotonic ramp, an irregular spike pattern).
- `ramp-profile-builder.html`: a standalone, backend-less HTML form that generates the same profile YAML.
- Full spec/plan: `docs/superpowers/specs/2026-10-14-distributed-ramp-load-generation-design.md` / `docs/superpowers/plans/2026-10-14-distributed-ramp-load-generation.md`.
- This went through a full fresh-context review (2 Critical + 9 Important findings, all either fixed-and-verified or explicitly ruled-and-disclosed — see the plan's final commits for the complete list if you want the gory detail).

### 3. Grafana dashboard expansion (shipped third, same day)
- `grafana/dashboards/jmeter-performance.json`: grew from 4 panels to 16 — KPI stat row (Active Threads, Total Requests, Throughput RPS, Throughput TPS, Error Rate), percentile stat row (p90/p95/p99), trend charts (Request Rate, Response Time Percentiles, Error Rate, Active Threads, **Connect Time vs Latency vs Response Time**), an Endpoint-Level Summary table, a Top Slowest Endpoints bar gauge, an Error Tracking by Code panel.
- `test-plans/{rest,graphql,soap}/ramp.jmx` also gained two new Prometheus collectors: `jmeter_connect_time_ms` and `jmeter_latency_ms` (both histograms, same `label,code` dimensions as the existing `jmeter_response_time_ms`). **This is the piece most likely relevant to you** — see "The OTel connection point" below.

---

## Lessons that generalize beyond this project (the part worth actually reading)

### Lesson 1 — Prometheus writes a staleness marker ~3-4 seconds after a scrape target's last successful scrape, once the target disappears from service discovery

If you query an instant Prometheus value using a timestamp that's even a few seconds past a target's death, you get nothing back — not because the data wasn't collected, but because Prometheus explicitly marks the series stale shortly after the target vanishes, and an instant query past that mark sees nothing regardless of the normal 5-minute lookback window. Confirmed via direct binary search against a live instance (query at the last real sample's timestamp → data; +4s → nothing; still nothing at +60s or +5min, ruling out the lookback-window excuse).

This bit this project twice:
- Once in the base stack (`run-test.sh`'s summary queries), fixed by querying at a **historical `time=` parameter, not "now."**
- Once in `ramp.py`'s own report (a 4-instance step's entire row silently vanished — 6/8 rows instead of 8 — with zero warning, because `step_end` is captured right as the container dies, landing right on that staleness edge essentially every time). Fixed with a `QUERY_SETTLE_SEC` buffer (one scrape interval, subtracted from the query instant before querying).

**If your `perf/` stack does any of its own report-time Prometheus querying** (not just live dashboarding), check whether it's vulnerable to this — it's an easy, silent failure mode, and "the number dropped" can look like a real performance finding instead of a staleness artifact.

### Lesson 2 — A single unaggregated Prometheus series from a multi-instance query is not a sum

`query_report_rows()` in `ramp.py` originally read `samples`/`avg_ms`/`error_pct` via `_first_value(result[0])` on a query scoped to `run_id, step_id, label` — which, with N parallel instances, returns N series (one per instance), and `result[0]` is an arbitrary one of them, not a total. A 4-instance step reported *fewer* samples than a 1-instance step — the report showed load collapsing as you scaled up, the exact opposite of reality. Fixed by wrapping every such query in `sum(...)`.

**Generalizable rule:** any PromQL query against a metric labeled by `instance` (or similar per-source labels) that doesn't explicitly `sum()`/`avg()`/`by()` across those instances is picking one arbitrary instance, not aggregating — easy to miss because the query still "returns a number," just the wrong one.

### Lesson 3 — Docker Compose's `--use-aliases` registers a container under its *service name*, which collides with any other job using that same name as a static scrape target

`ramp.py` launches instances via `docker-compose run --use-aliases --name <deterministic-name> jmeter ...` — `--use-aliases` is required so Prometheus's file-based service discovery can resolve the deterministic container name. But it *also* registers the container under the generic `jmeter` service alias, which a pre-existing static scrape job (`job=jmeter`, target `jmeter:9270`) was *also* pointed at. Result: that static job silently scraped whichever ramp instance happened to be up at the time, producing a non-monotonic, unattributable (no `run_id`/`step_id`) counter mixed into an otherwise-clean, already-validated base-project metric series. Confirmed via a live range query showing the counter repeatedly resetting downward — the unmistakable signature of DNS round-robin between multiple real containers sharing one alias.

Fixed by giving ramp instances their own dedicated compose service (`jmeter-ramp`, identical config, different name) so the two scrape jobs can never resolve to each other's containers.

**If your `perf/` stack runs multiple services under `docker-compose run --use-aliases` that might share a service name with anything else being scraped**, this is worth a direct check — it won't error, it'll just quietly corrupt a metric.

### Lesson 4 — `jmeter-prometheus-plugin` 0.7.1's Transaction Controller "generate parent sample" option trades away per-sampler visibility, with no known way to get both

Wanted: a real TPS (transactions/sec) metric distinct from RPS (requests/sec), for flows that chain 2+ HTTP samplers per iteration. The textbook-correct JMeter mechanism is a Transaction Controller with `TransactionController.parent=true`, which generates one aggregated "transaction" sample. **Confirmed empirically this plugin stops exposing the individual child-sampler labels entirely once `parent=true` is set** — only the parent's own label survives in `jmeter_requests_total`. A follow-up attempt (a second `PrometheusListener` scoped inside the Transaction Controller, to recover per-sampler visibility alongside the transaction rollup) didn't crash, but silently corrupted actual test throughput by ~3 orders of magnitude (`summary = 3 in 00:00:15` instead of several thousand) — reverted immediately.

**If you want both real transaction-level timing *and* per-endpoint breakdown with this plugin, the two seem to be mutually exclusive.** The workaround shipped here: filter the existing RPS metric down to just each flow's first sampler (fires exactly once per iteration), which gives an exact iteration-count proxy with zero `.jmx` risk, at the cost of its reported latency being just that one hop's time rather than the full chain's.

### Lesson 5 — PromQL regex literals need escaping twice, and a JSON-embedded query needs it a third time

To match a literal `+` via Prometheus's `=~` matcher, the **PromQL query text itself** needs `\\+` (two backslashes) — PromQL's own string-literal parser consumes one escaping layer before the regex engine sees the pattern; a single `\+` fails with `parse error: unknown escape sequence`. Since that query text lives inside a JSON string in a Grafana dashboard file, the JSON source needs **4** backslash characters to produce **2** actual backslashes in the decoded PromQL text. Got this wrong once (reduced it to 2, which was syntactically valid PromQL but matched the wrong thing — silently, no error) before testing directly against live data caught it.

**If your dashboards embed any regex with special characters (`+`, `.`, `(`, etc.) inside JSON-stored PromQL, this is worth testing directly against live Prometheus, not just confirming the JSON parses** — a wrong-but-valid regex produces no error at any layer.

---

## The OTel connection point (why this handoff mentions OTel at all)

When scoping the dashboard work, the user asked whether the new `jmeter_connect_time_ms`/`jmeter_latency_ms` collectors should also go on the base project's *starter* test plans (not just the ramp variants). Decision: **ramp variants only, here** — extending to the starters (or to any OTel-correlated view of this same data) "would make more sense alongside OTel integration" in your project, not this one.

Concretely, what exists now: `test-plans/{rest,graphql,soap}/ramp.jmx` each export, per sampler, three histograms —
- `jmeter_response_time_ms` (total elapsed time)
- `jmeter_connect_time_ms` (TCP connection setup)
- `jmeter_latency_ms` (time to first byte / server processing)

— all with `label` (sampler name) and `code` (HTTP status) dimensions, confirmed live and correct across all three protocols (`jmeter-prometheus-plugin`'s `ListenerCollectorConfig$Measurable` enum genuinely supports `ConnectTime`/`Latency`, not assumed — extracted and confirmed from the plugin jar directly before using them).

If your `perf/` stack correlates JMeter load-test metrics with OTel traces from the actual Boomi runtime/gateway, this three-way breakdown (connect vs. server-processing vs. total) is exactly the kind of thing that's useful to line up against OTel span timings on the same request — e.g., confirming whether a latency spike is network-side (connect time), server-side (JMeter's "latency"/TTFB), or something downstream of the first byte (the gap between latency and total response time). This repo doesn't do that correlation itself (explicitly out of scope here), but the raw metric is now real and verified, in case it's a useful building block.

---

## Current state / what's NOT done here

- Everything described above is pushed to `main` as of commit `14b53ca`.
- Two other sub-projects remain unscoped on this side: **real Boomi target integration** and **CI/CD** (the user already has Jenkins containers + GitHub Actions assets from other work, per this project's own memory) — these were explicitly named as "next" when the ramp/spike work started, not yet picked up.
- This project's own memory file (for whichever session continues it) lives at `~/.claude/projects/-Users-shahabdullahmac-Documents-boomicompanion-projects-BOOMI-Performance-Testing-POC/memory/project_boomi-load-testing.md` if a future session here wants the full blow-by-blow (every ruling, every timing-margin finding, more detail than this handoff includes).

No action is expected from your side in response to this document — it's context to have on hand when the user brings the two projects into the same conversation again.
