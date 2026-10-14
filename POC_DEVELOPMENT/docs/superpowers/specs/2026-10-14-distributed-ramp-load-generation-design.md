# Distributed Ramp Load Generation — Design

**Status:** Approved design, pending implementation plan
**Date:** 2026-10-14

## Context

This is the first of three sub-projects the user scoped toward pushing the existing JMeter+Prometheus+Grafana performance-testing stack (`docs/superpowers/specs/2026-10-12-jmeter-boomi-performance-stack-design.md`, fully implemented, reviewed, and fixed as of 2026-10-14) "close to a production enterprise level testing ability." The three named pieces — distributed load generation, real Boomi target integration, and CI/CD — were flagged as independent subsystems during brainstorming and are being decomposed into separate spec → plan → implementation cycles. This spec covers **distributed load generation only**. Real Boomi target integration and CI/CD are out of scope here and will get their own specs.

The existing stack runs exactly one JMeter instance at a time, as a one-shot `docker-compose run` invocation (`run-test.sh`). That's sufficient for the correlation-demo starter templates it was built to validate, but not for generating meaningful load at scale, or for a ramp-style capacity test (increasing load across steps, stopping automatically if error rate or latency crosses a threshold) like the kind the separate, heavier `perf/` project already has via its own `ramp.sh`.

**Target environments, in order of priority:** a single machine (Docker Compose, scaling via multiple parallel container instances) first; a VM cluster and Kubernetes later, reusing the same core mechanism behind a different "launcher." This spec designs the core mechanism to be launcher-agnostic and fully specifies the single-machine launcher; the VM-cluster and Kubernetes launchers are explicitly deferred (see Non-goals) but the design should not need to change shape to accommodate them later.

**Ramp dimensions:** both the number of parallel JMeter instances and the thread count per instance are independently configurable per step (not just one or the other), confirmed explicitly during brainstorming.

## Goals

1. Run **multiple JMeter instances in parallel**, all tagged with the same `run_id`, generating load against the same target simultaneously — with each instance's metrics individually visible in Prometheus/Grafana (not blended into a single series), so per-instance fairness/behavior can be inspected if needed.
2. Support a **ramp profile**: an ordered sequence of steps, each specifying an instance count and a thread-count-per-instance, run for a configured duration. Both dimensions may change between steps.
3. **Stop automatically** if a step's steady-state (post-warm-up) error rate or average response time exceeds a configured threshold, rather than running the full profile regardless of what the target can handle.
4. Produce a **summary report** at the end (one row per step: instances × threads, throughput, avg/p95 response time, error rate) pulled from Prometheus — not something the user has to piece together from the Grafana dashboard by hand.
5. Keep the **single-machine launcher** (this spec's actual deliverable) as thin and swappable as possible, so a VM-cluster or Kubernetes launcher can reuse the same core mechanism later without redesigning it.
6. Offer **two ways to build a ramp profile**: hand-editing a YAML file directly (the baseline, "developer" path), or a lightweight static HTML form that generates the same YAML (a convenience layer, not a replacement) — mirroring the dual-authoring-path pattern already established for `.jmx` test plans in the base project.

## Non-goals (explicitly deferred)

- **VM-cluster and Kubernetes launchers.** The core mechanism (ramp step format, file-based service discovery, sustained-load test plans, stop-rule logic) is designed to extend to both, but only the single-machine (Docker Compose) launcher is built in this round.
- **A Helm chart.** Still deferred from the original spec; doubly so here since Kubernetes itself isn't in scope yet.
- **Real Boomi target integration and CI/CD.** Separate sub-projects, each getting its own spec.
- **A full web UI/backend for ramp-profile authoring.** Only a static, backend-less HTML page that generates a YAML file for the user to save — not a hosted service with its own state.
- **Cross-run historical comparison dashboards specific to ramp data** beyond what the existing Grafana dashboard and `run_id`/`step_id` labels already provide. A dedicated ramp-results Grafana panel is a reasonable follow-up, not built here.

## Architecture

### The scraping problem this design has to solve first

The existing stack's `prometheus.yml` scrapes one static target (`jmeter:9270`), which only works because exactly one JMeter container exists at a time, made reliably resolvable via `docker-compose run --use-aliases`. The moment multiple instances run **simultaneously**, they can't all resolve to that same hostname, and Prometheus has no way to discover however many instances currently exist.

**Fix: switch Prometheus to file-based service discovery** (`file_sd_configs`), the same mechanism the separate `perf/` project already uses and has validated, applied here to a new problem (multiple simultaneous targets instead of one changing target). Each launched instance gets its own small JSON target file, written to a shared directory bind-mounted into the Prometheus container, immediately before that instance is launched:

```json
[
  {
    "targets": ["<instance-container-name>:9270"],
    "labels": { "run_id": "<run_id>", "step_id": "<step_id>" }
  }
]
```

`prometheus.yml`'s scrape config changes from a static target list to:

```yaml
scrape_configs:
  - job_name: "jmeter-ramp"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/*.json"]
        refresh_interval: 5s
```

Prometheus re-reads that directory on its own (`refresh_interval`); no reload or restart needed when files are added or removed. `docker-compose.yml` needs a new bind mount (`./prometheus/targets:/etc/prometheus/targets`) shared between the host (where `ramp.sh` writes/deletes files) and the Prometheus container.

Each instance needs a **deterministic, known-in-advance container name** so `ramp.sh` can write its target file before launching it — `docker-compose run --name <derived-name> --use-aliases jmeter ...`. Whether `--name` and `--use-aliases` compose cleanly together (both are flags on the same `run` invocation) needs to be verified empirically during implementation; if a conflict surfaces, the fallback is to launch first, read the real assigned name via `docker inspect`, then write the target file — accepting a short window where that one instance isn't yet scrape-visible.

Since each instance's target file uses a unique container name as its address, Prometheus's own `instance` label (derived automatically from the scrape target address) distinguishes per-instance metrics for free — no separate instance-ID property needs to be threaded through the test plans.

### Ramp profile format

A small YAML file, passed to `ramp.sh` by path (e.g. `./ramp.sh ramp-profiles/rest-standard.yaml`), so multiple named profiles (a quick smoke profile, a full capacity profile, etc.) can coexist without re-typing parameters:

```yaml
test_plan: rest/starter.jmx
step_duration_sec: 60
warmup_sec: 15
stop_rule:
  max_error_rate_pct: 2
  max_avg_response_ms: 3000
steps:
  - instances: 1
    threads: 5
  - instances: 2
    threads: 5
  - instances: 2
    threads: 10
  - instances: 4
    threads: 10
```

`warmup_sec` excludes that portion of each step's window from both the stop-rule evaluation and the final summary report — the raw samples still land in Prometheus untouched; they're simply excluded from the "steady-state" numbers that decide whether to proceed, matching the reasoning the separate `perf/` project already uses for the same purpose.

**Two ways to build this file:** hand-edit the YAML directly (always available, no extra tooling — the "developer option"), or use a lightweight static HTML page (no backend, similar in spirit to earlier reference pages built for this project) with form fields for steps/thresholds/durations that generates the same YAML content for the user to save. The HTML form is a convenience layer on top of the YAML format, not a replacement for it — both paths produce and consume the identical file.

### New test plan variant: sustained load

The existing starter templates (`test-plans/{rest,graphql,soap}/...`) are single-shot — one loop, a handful of requests, built for quick correlation demos, not sustained load. Rather than stretch them to serve both purposes, each protocol gets a new sibling test plan for ramping: `test-plans/rest/ramp.jmx`, `test-plans/graphql/ramp.jmx`, `test-plans/soap/ramp.jmx` — cloned from the corresponding starter's correlation/assertion logic, with the Thread Group reconfigured for property-driven, duration-based looping instead of a fixed single loop:

- `LoopController.continue_forever` → `true`
- `ThreadGroup.scheduler` → `true`
- `ThreadGroup.duration` → `${__P(DURATION,60)}`
- `ThreadGroup.num_threads` → `${__P(THREADS,1)}`

Everything else (HTTP Request Defaults, Authorization/Header Manager using `${__groovy(System.getenv(...))}`, JSON/Regex extractors, response assertions, the Prometheus Listener with its `jmeter_requests_total`/`jmeter_response_time_ms`/`jmeter_failures_total` collectors) carries over unchanged from the corresponding starter, **with one deliberate difference**: the starters bake `run_id` into each collector's own `Labels` column (`label,code,run_id`) because their scrape target is static and `run_id` has to come from somewhere. The ramp variants drop `run_id` from the collector labels (`label,code` only) and carry **no `step_id` property or UDV inside the `.jmx` at all** — both `run_id` and `step_id` are supplied uniformly to every metric scraped from a given instance via that instance's file-discovery target labels (see above), avoiding two different label sources disagreeing about the same name. `ramp.sh` still passes `-JTHREADS`/`-JDURATION` (which the Thread Group reads), but has no need to pass run/step identifiers into the JMeter process itself — it already holds those values in its own shell variables for constructing each instance's target file and result filenames directly. The 15-second pre-Listener pause that the starters need (to survive long enough for one Prometheus scrape) is **not needed** here either — a sustained, duration-based test plan is alive far longer than one scrape interval on its own, so the pause element is omitted from the ramp variants entirely.

### Orchestration flow (`ramp.sh`)

For each step in the YAML, in order:

1. Generate a `step_id` (e.g. `step-01`, `step-02`, ...).
2. For each of that step's `instances`: derive a deterministic container name, write that instance's Prometheus target file (labeled with `run_id` and `step_id` — the only place those values need to exist, per the labeling note above), then launch it as a backgrounded `docker-compose run --rm --use-aliases --name <derived-name> jmeter -n -t <ramp-plan> -JTHREADS=<threads> -JDURATION=<step_duration_sec>`.
3. Wait for all of that step's instances to exit (they self-terminate via the scheduler once `DURATION` elapses — no external kill signal needed).
4. Delete that step's target files.
5. Query Prometheus (a **range** query covering `[step_start + warmup_sec, step_end]`, scoped to this `step_id` — an instant query would show nothing once the ephemeral instances are gone, per the staleness lesson from the base project) for steady-state error rate and average response time.
6. If either exceeds the profile's `stop_rule` threshold: stop, report which step failed and why, skip remaining steps.
7. Otherwise, advance to the next step.

At the end (whether completed in full or stopped early), print a summary table: one row per step actually run, with instances × threads, measured throughput, avg/p95 response time, and error rate, each pulled from Prometheus using the same step-scoped range-query approach.

## Testing & validation

1. **File-based service discovery, in isolation**: launch 2-3 throwaway instances with distinct container names and target files, confirm Prometheus's `/api/v1/targets` shows all of them simultaneously, confirm removing a target file makes that instance disappear from Prometheus's target list within one `refresh_interval` without any reload.
2. **Sustained-load test plan, in isolation**: run one `ramp.jmx` instance directly with a short `DURATION` (e.g. 20s) and `THREADS=3`, confirm it actually runs for roughly that wall-clock duration (not instantly, not indefinitely) and produces the expected request volume.
3. **Full ramp profile, small scale**: a 2-step profile (e.g. 1 instance/5 threads, then 2 instances/5 threads) against a bundled sample target, confirming the summary table's per-step numbers are plausible and that `step_id` genuinely distinguishes the two steps in Prometheus (same technique used to verify `run_id` differentiation in the base project).
4. **Stop-rule firing correctly**: deliberately configure an unreasonably low `max_avg_response_ms` (e.g. 1ms) for one step, confirm `ramp.sh` stops after that step and does not proceed further, and that the reported reason names the actual measured value against the threshold.
5. **Deterministic naming + `--use-aliases` compatibility**: explicitly verify (don't assume) that `docker-compose run --name <x> --use-aliases jmeter ...` works as expected before building the rest of `ramp.sh` around that assumption; if it doesn't compose cleanly, fall back to the launch-then-inspect approach noted above and update this spec.

## Open items carried forward (not blocking this design)

- Whether a dedicated Grafana dashboard/panel for ramp-specific views (per-step comparison, instance-count-over-time) is worth building once this is in use, versus relying on the existing dashboard's `run_id` filtering.
- The VM-cluster and Kubernetes launchers themselves — this spec only commits to not designing the core mechanism in a way that would need to change shape for them.
- Whether `ramp.sh`'s summary report should also write a machine-readable file (JSON/CSV) for later analysis, versus only printing a human-readable table.

## Collaboration mode

Per established project convention: the user hand-types every real deliverable file themselves, decided ad hoc per file (default is hand-typed; Claude writes a specific file directly only when asked for that file, not as a standing rule change). This spec document is a Claude-authored exception, as planning/design documentation rather than a real implementation deliverable.
