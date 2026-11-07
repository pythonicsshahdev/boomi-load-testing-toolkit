# Operations notes

JMeter + Prometheus + Grafana load-testing stack for Boomi-published REST,
SOAP, and GraphQL APIs. See `docs/design/specs/2026-10-12-jmeter-boomi-performance-stack-design.md`
for the full design.

## Authoring test plans

Two ways to build/edit `.jmx` files under `test-plans/`, same shared folder either way:

1. **Local JMeter GUI** — if you already have JMeter installed (e.g. via
   `brew install jmeter`), you **must** first install the
   [`jmeter-prometheus-plugin`](https://github.com/johrstrom/jmeter-prometheus-plugin)
   jar (same version/source the `jmeter` image uses — see `docker/jmeter/Dockerfile`)
   into your local JMeter's `lib/ext/` directory (for Homebrew on Apple
   Silicon, typically
   `/opt/homebrew/Cellar/jmeter/<version>/libexec/lib/ext/`). Without it,
   JMeter's GUI cannot even *open* any of this repo's `.jmx` files — they
   all reference a `PrometheusListener` class that only exists once the
   plugin jar is present, and JMeter fails with
   `CannotResolveClassException` on load, not just on run. Once that's in
   place, just open files directly from `test-plans/`.
   Optional: also install [JMeter Studio](https://github.com/QAInsights/jmeter-studio)
   for the same modernized theme the `jmeter-gui` container ships with —
   download the latest jar from its Releases page, copy it into the same
   `lib/ext/` directory, restart JMeter, then Options → Themes.
2. **Browser-based GUI** — run `docker-compose up -d jmeter-gui`, then open
   `http://localhost:6080/vnc.html`. No local JMeter install needed.
   Published ports (6080, 9090, 3004, 8010, 8011) are bound to `127.0.0.1`
   by default — fine for local use; if you deploy this on a remote VM and
   need to reach these from elsewhere, change the `127.0.0.1:` prefix in
   `docker-compose.yml`'s `ports:` entries, and set a VNC password in
   `docker/jmeter-gui/supervisord.conf` (the `-nopw` flag) before doing so, since
   that desktop can see this project's real credentials.

## First-time setup

```bash
cp .env.example .env
```

`.env` is gitignored, so this file doesn't exist right after a fresh clone.
`docker-compose.yml` treats it as optional (`required: false`), so the
stack still starts without it — but `run-test.sh`/`smoke-test.sh` expect
it to be present, so create it before running either.

## Running a test

```bash
./run-test.sh rest/starter.jmx
```

Generates a `RUN_ID`, runs the test headlessly, and prints the id to filter
by in Grafana (`http://localhost:3004`, login `admin`/`admin`, dashboard
"Boomi Performance Testing — JMeter Overview").

To check the whole stack end-to-end (including that metrics actually reach
Prometheus, not just that services respond), run `./smoke-test.sh`.

**If you run `docker-compose run jmeter ...` yourself** (instead of through
`run-test.sh`), you must add `--use-aliases`: `docker-compose run --rm
--use-aliases jmeter ...`. Without it, Docker's embedded DNS never
registers the `jmeter` hostname for a one-off `run` container, so
Prometheus can never resolve it — the test still runs and reports success,
but exports zero metrics, with no error anywhere. This was the single most
expensive thing to track down while building this stack; `run-test.sh`
already includes the flag.

## Running a distributed ramp/spike test

`ramp.sh` drives multiple parallel JMeter instances per step (ramp: a monotonic
increase; spike: an irregular up-and-down pattern — both are the same
mechanism, just different profiles), with automatic stop-on-threshold and a
JMeter-native per-label summary report. Separate from `run-test.sh`'s
single-shot starters.

One-time setup (creates its own virtualenv, kept separate from the rest of
this project since it's Python, not JMeter):

```bash
python3 -m venv .venv-ramp          # Python 3.9 or newer
source .venv-ramp/bin/activate
pip install -r requirements-ramp.txt
```

Then:

```bash
./ramp.sh profiles/rest-ramp-example.yaml      # a 4-step monotonic ramp
./ramp.sh --yes profiles/rest-spike-example.yaml  # a 5-step spike pattern
```

A profile is a YAML file (see `profiles/*.yaml` for more examples, or
build one visually in `ramp-profile-builder.html` — open it directly in a
browser, no server needed):

```yaml
test_plan: rest/ramp.jmx       # or graphql/ramp.jmx, soap/ramp.jmx
step_duration_sec: 30          # default duration for steps that don't override it
warmup_sec: 5                  # excluded from every step's reported metrics
slow_request_threshold_ms: 2000  # optional; omit to disable slow-request detection
stop_rule:
  max_error_rate_pct: 2
  max_avg_response_ms: 3000
steps:
  - instances: 1
    threads: 5
  - instances: 4
    threads: 10
    duration_sec: 60          # optional per-step override of step_duration_sec
```

Run `./ramp.sh --validate <profile.yaml>` first to check a profile without
launching anything. Above 4 simultaneous instances in any step, `ramp.sh`
asks for confirmation before launching anything (not just before the large
step) — pass `--yes` to skip this for unattended use.

Exit codes: `0` clean run, `2` the stop rule triggered, `3` at least one
instance exited non-zero, `4` regression against the baseline, `5` no usable data
(a step recorded no measurable samples). The summary report still prints in every case.

Results land in `test-plans/results/<run_id>-<step_id>-<idx>.jtl` (same
gitignored pattern as the starters' `.jtl` files); Grafana gets a
start/end annotation for every step (`http://localhost:3004`, same
`admin`/`admin` login), visible on the existing JMeter Overview dashboard.

Ramp instances run under their own compose service, `jmeter-ramp` — a
deliberate duplicate of the `jmeter` service under a different name/alias,
so the two scrape jobs in `prometheus.yml` (the starters' static `jmeter`
job and the ramp instances' file-based-discovery `jmeter-ramp` job) can
never resolve to each other's containers via `--use-aliases`.

Like the starters, `test-plans/{rest,graphql,soap}/ramp.jmx` already omit
the 15-second pause mentioned below — they're built to loop for their
configured duration, not for a single quick demo.

The ramp variants export two additional metrics beyond the starters'
`jmeter_requests_total`/`jmeter_response_time_ms`/`jmeter_failures_total`:
`jmeter_connect_time_ms` (TCP connection setup) and `jmeter_latency_ms`
(time to first byte / server processing time), both histograms with the
same `label,code` dimensions. The Grafana dashboard's KPI row, percentile
stats, Connect/Latency/Response-Time breakdown, endpoint summary table,
slowest-endpoints bar gauge, and per-code error tracking panels all read
from these plus the existing metrics — select a `run_id` (and optionally a
`step_id`) to filter. The endpoint table and slowest-endpoints panels use
instant Prometheus queries, so viewed within a few seconds of a run
finishing they can briefly show no data — same margin as `ramp.py`'s own
report; give it a moment or look at a run that's already fully settled.

## Credentials

Copy the `BOOMI_TEST_TARGET_*` variables from `.env.example` into
`.env` (not any other `.env` in this repo) and fill in real
values for whatever API you're testing. Test plans reference them via
`${__groovy(System.getenv("VAR_NAME"))}` in the Authorization Manager /
Header Manager — **not** `${__env(...)}`, which looks like it should work
but isn't a real JMeter function in a stock install; it silently resolves
to the literal placeholder text with no warning. `__groovy` is bundled
with JMeter core and is the pattern this repo actually uses.

## Before you clone a starter for a real test

The starter templates (`test-plans/rest/starter.jmx`, `graphql/starter.jmx`,
`soap/smoke-test.jmx`) each include a **15-second pause** (a `TestAction`
element, labeled "Pause 15s...") right before the Prometheus Listener.
It exists only so this project's own quick 1-2 request demos survive long
enough for Prometheus's 5-second scrape interval to catch their metrics
before the JVM exits — it is not a property real load tests need. A
sustained test (many threads/iterations over tens of seconds) stays alive
far longer than one scrape interval on its own. When you clone a starter
for a real test, **delete that pause element** (or your measured
throughput will include a 15-second sleep in every iteration).

**If you change `docker/grafana/dashboards/*.json` or anything under
`docker/grafana/provisioning/`** after the stack has been brought up once,
rebuilding the `boomi-loadtest-grafana` image and recreating the container is
**not enough** — `grafana-data` is a named volume that persists Grafana's
own database across container recreation, and Grafana already imported
the old dashboard into that database on first boot. Rebuilding the image
silently does nothing until you also wipe that volume:

```bash
docker-compose stop grafana
docker-compose rm -f grafana
docker volume rm boomi-loadtest_grafana-data
docker-compose up -d grafana
```

**Linux hosts:** the `jmeter` and `jmeter-gui` containers run as root
(the base image's default), so `.jtl` result files and `jmeter.log`
written into the bind-mounted `test-plans/` folder land root-owned on a
Linux host. This is invisible on macOS (Docker Desktop/Colima remap
ownership), which is why the dual-authoring round-trip checks passed
cleanly here — but on Linux, those files become unwritable/undeletable
from your local user account, and a `.jmx` saved from the `jmeter-gui`
session could become unwritable from the local GUI too. If you deploy
this on a Linux VM, add `user: "${UID:-1000}:${GID:-1000}"` to the
`jmeter` and `jmeter-gui` services in `docker-compose.yml`, or run
`export UID GID` before `docker-compose up` if your shell doesn't already
export them.

## Known limitation: Prometheus Listener GUI panel

Opening a `.jmx` that contains a Prometheus Listener element (every starter
template does) and selecting that specific tree node shows a dismissible
error — `Cannot invoke "org.apache.jmeter.gui.JMeterGUIComponent.clearGui()"
because "guicomp" is null` — in both the local GUI and the `jmeter-gui`
noVNC container. This reproduces identically on Java 17 and Java 21, so
it isn't an environment issue; it appears to be a quirk in
`jmeter-prometheus-plugin` 0.7.1's own GUI registration. It's cosmetic:
dismissing the dialog leaves the rest of the tree (samplers, assertions,
extractors, variables) fully usable, and it has no effect on actually
running tests — every test plan in this repo executes and reports
correctly regardless. If you're building a new test plan, copy the
Prometheus Listener element from an existing starter rather than
configuring one from scratch through its GUI panel.
