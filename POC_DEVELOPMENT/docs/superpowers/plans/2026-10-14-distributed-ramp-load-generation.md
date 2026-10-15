# Distributed Ramp & Spike Load Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **COLLABORATION MODE OVERRIDE — READ BEFORE STARTING ANY TASK:** Same as the base project's plan: the user hand-types every real deliverable file themselves (Dockerfiles, `docker-compose.yml`, Python/bash scripts, `.jmx` test plans, YAML profiles — everything under each task's **Files: Create/Modify**). In practice this session, it's been ad hoc per file — default is hand-typed, but the user sometimes asks the executor to write a specific file directly, which is a one-off, not a standing rule change. The executor's job for every file is to give exact content, wait for it to be saved, then verify it (read it back, or run a build/test command that would fail loudly on a mistake) — never assume Write/Edit is the default. Verification commands (`docker build`, `docker-compose up`, `pytest`, `curl`, etc.) are not file-authoring and run normally.

**Goal:** Add distributed, ramp/spike-pattern load generation to the existing JMeter+Prometheus+Grafana stack — multiple parallel JMeter instances per step, both instance-count and per-instance thread-count configurable per step (plus per-step duration, enabling non-monotonic spike patterns), automatic stop-on-threshold, a JMeter-native Aggregate-Report-style summary broken down per sampler label per step, individual slow-request detection, and five operational-robustness mechanisms (interrupt cleanup, a confirmation gate, load-generator saturation detection, a `--validate` mode, and Grafana step annotations).

**Architecture:** A new Python orchestrator (`ramp.py`, invoked via a thin `./ramp.sh` wrapper to match the spec's naming) drives the existing `jmeter` image through parallel `docker-compose run --name <deterministic> --use-aliases` invocations per step, coordinating via Prometheus's file-based service discovery (new — the base project's static single-target scrape config can't discover N simultaneous instances) and new sustained-load `.jmx` variants per protocol (looping for a configured duration instead of the existing starters' single-shot pattern). Python was chosen over pure bash for this orchestrator specifically because of the structured-data handling involved (YAML profile parsing, JSON HTTP responses from Prometheus/Grafana, CSV `.jtl` parsing, signal handling) — bash string-parsing of JSON/YAML is exactly the kind of fragile pattern this project's own `smoke-test.sh`/`run-test.sh` have so far avoided needing, and this orchestrator needs much more of it than either of those scripts does.

**Tech Stack:** Python 3.14 (already installed via Homebrew, same interpreter used for `graphql-target`'s tests), PyYAML, the existing `boomi-jmeter:dev` image (unchanged), Prometheus file-based service discovery, Grafana's REST Annotations API.

**Spec:** `docs/superpowers/specs/2026-10-14-distributed-ramp-load-generation-design.md`

## Global Constraints

- Single-machine (Docker Compose) launcher only this round; VM-cluster and Kubernetes launchers are out of scope (spec Non-goals) — this plan's job is to not design the core mechanism in a way that would need to change shape for them later, not to build them.
- Both `instances` and `threads` are independently configurable per step; `duration_sec` is also per-step-overridable (falls back to the profile's top-level `step_duration_sec` when a step omits it) — this is what makes both classic ramps and irregular spike patterns expressible with one schema.
- **Verified empirically while writing this plan:** `docker-compose run --name <derived> --use-aliases jmeter ...` works exactly as the spec hoped — the container is reachable by both its custom name and the generic `jmeter` alias simultaneously. No fallback to launch-then-inspect is needed.
- **Also discovered while verifying the above:** a stale, not-yet-removed container occupying a name `ramp.py` wants to reuse causes `docker-compose run --name <x> ...` to fail with a conflict error. Every instance launch must defensively `docker rm -f <name>` first (ignoring "no such container" errors), not just trust prior `--rm` cleanup.
- Ramp/spike test plans (`test-plans/{rest,graphql,soap}/ramp.jmx`) carry **no `run_id` or `step_id` inside the `.jmx` at all** — both come only from each instance's Prometheus file-discovery target labels, to avoid two different label sources (metric-level vs. target-level) disagreeing about the same name. The existing starters' pattern (baking `run_id` into the Prometheus Listener's own `Labels` column) is correct for *them* and must not change; the ramp variants are simply different on this one point.
- The Prometheus Listener's collector labels on the ramp variants are `label,code` only (no `run_id`), matching the point above.
- **Verified empirically:** Grafana's Annotations API is `POST /api/annotations` with JSON body `{"time": <epoch-ms>, "tags": [...], "text": "..."}`, using the same `admin`/`admin` credentials already used elsewhere in this project; a plain `GET /api/annotations?tags=<tag>` reads them back. No dashboard-specific wiring needed — annotations are global by default.
- **Verified empirically:** `docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}"` gives per-container CPU usage (e.g. `1.21%`); `sysctl -n hw.ncpu` gives the host's core count on this machine (macOS). Both are used for the load-generator saturation check.
- **Verified empirically:** an **instant** Prometheus query with an explicit historical `time=<epoch-seconds>` parameter (not "now") correctly evaluates `increase()`/`histogram_quantile()` as of that past moment, without the "ephemeral target already gone" staleness problem that plagued plain "now" instant queries throughout the base project. This is the mechanism every step's reporting/stop-rule query in this plan uses.
- Confirmation-gate default threshold: 4 instances (spec's suggested default) — configurable in code, not meant to be a profile-level YAML key (it's an operator safety setting, not part of a profile's own load-shape description).
- JMeter-native Aggregate Report columns actually printed: Label, # Samples, Average, Median, 90% Line, 95% Line, 99% Line, Error %, Throughput — broken down per sampler label, per step (never blended across labels within a step). Min/Maximum are intentionally omitted: Prometheus histograms only expose bucket boundaries, not a true per-window min/max, and adding a new collector type to recover them is out of scope (no new collectors, per the metric-names constraint below).
- Metric names unchanged from the base project: `jmeter_requests_total`, `jmeter_response_time_ms` (buckets `50,100,250,500,1000,2500,5000,10000`), `jmeter_failures_total`. No new metrics or collectors are needed for any of this plan's reporting — only new PromQL queries at report time.
- The existing base-project `jmeter` scrape job (static target `jmeter:9270`, used by `run-test.sh`/`smoke-test.sh`/the starters) **must keep working unchanged** — this plan adds a second, file_sd-based scrape job alongside it, never replaces it.

## Review Focus

- **A step where one of several parallel instances exits early or crashes (e.g. OOM) while its siblings keep running.** The orchestrator's "wait for all instances" logic must notice and report a non-zero exit code from any instance, not silently treat an early, incomplete run as a normal completion — a reasonable person reading a "step complete" report would expect to be told if one of N instances didn't actually finish its work.
- **A step whose Prometheus query returns no data at all** (e.g. every instance died before a single scrape happened). The stop-rule/report-generation code must handle an empty PromQL result vector without crashing (a `KeyError`/`IndexError` on an empty `result` list is an easy mistake) and must treat "no data" as a reportable condition, not silently skip the step from the summary.
- **Running `ramp.py` with no TTY and no `--yes`, above the confirmation-gate threshold** (e.g. later from an unattended context). It must fail fast with a clear message telling the operator to pass `--yes`, not hang forever on a blocking input prompt nobody can answer.
- **Two instances in the same step writing target files into the shared directory at nearly the same wall-clock moment.** Each instance's target filename must be unique by construction (incorporating `run_id`, `step_id`, and its own instance index) — a reasonable person would expect two simultaneously-launched instances to never silently overwrite each other's Prometheus target file.
- **A profile whose `steps` list is empty, or a step missing a required key** (`instances`/`threads`). `--validate` must catch this and name the specific problem; running such a profile without `--validate` first must not silently do nothing or crash with an unhelpful Python traceback — it should fail the same validation check before launching anything.

---

### Task 1: Prometheus file-based service discovery

**Files:**
- Modify: `prometheus/prometheus.yml`
- Modify: `docker-compose.yml` (add a bind mount to the `prometheus` service)
- Executor may create directly (trivial, no real content): `prometheus/targets/.gitkeep`, `ramp-profiles/.gitkeep`

**Interfaces:**
- Produces: a second Prometheus scrape job (`jmeter-ramp`, file-based service discovery watching `/etc/prometheus/targets/*.json`) alongside the existing static `jmeter` job, which every later task's instance-launching code relies on for getting scraped at all.

- [ ] **Step 1: Create the scaffolding directories**

```bash
mkdir -p prometheus/targets ramp-profiles
touch prometheus/targets/.gitkeep ramp-profiles/.gitkeep
```

- [ ] **Step 2: Give the user the exact new content for `prometheus/prometheus.yml`**

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: "jmeter"
    static_configs:
      - targets: ["jmeter:9270"]
  - job_name: "jmeter-ramp"
    file_sd_configs:
      - files: ["/etc/prometheus/targets/*.json"]
        refresh_interval: 5s
```

Ask the user to save this as `prometheus/prometheus.yml`, **replacing** the existing content (the first job is unchanged from before; the second job is new).

- [ ] **Step 3: Give the user the exact addition to `docker-compose.yml`**

In the `prometheus` service's `volumes:` list, add one line (alongside the existing `prometheus.yml` and `prometheus-data` mounts):

```yaml
      - ./prometheus/targets:/etc/prometheus/targets:ro
```

- [ ] **Step 4: Recreate Prometheus and confirm both scrape jobs exist**

```bash
docker-compose up -d --force-recreate prometheus
sleep 3
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json,sys
d = json.load(sys.stdin)
pools = set(t['scrapePool'] for t in d['data']['activeTargets'])
print('scrape pools found:', pools)
assert 'jmeter' in pools, 'existing static job missing!'
assert 'jmeter-ramp' in pools, 'new file_sd job missing!'
print('both jobs present')
"
```

Expected: `both jobs present`. If `jmeter-ramp` is missing, double check the bind mount path matches `files: ["/etc/prometheus/targets/*.json"]` exactly.

- [ ] **Step 5: Prove file-based discovery actually works, with real throwaway instances (not yet via any new script — just manual commands, to isolate this mechanism from anything built in later tasks)**

```bash
# Launch two throwaway instances with deterministic names (reusing the
# existing infra-check fixture — no new test plan needed for this proof)
docker rm -f ramp-proof-1 ramp-proof-2 2>/dev/null; true
docker-compose run --rm --use-aliases --name ramp-proof-1 jmeter -n -t /test-plans/_infra-check/empty.jmx > /tmp/ramp-proof-1.log 2>&1 &
PID1=$!
docker-compose run --rm --use-aliases --name ramp-proof-2 jmeter -n -t /test-plans/_infra-check/empty.jmx > /tmp/ramp-proof-2.log 2>&1 &
PID2=$!
sleep 3

# Write both target files only now that we know the containers are up
cat > prometheus/targets/proof-1.json <<'EOF'
[{"targets": ["ramp-proof-1:9270"], "labels": {"run_id": "proof", "step_id": "step-01"}}]
EOF
cat > prometheus/targets/proof-2.json <<'EOF'
[{"targets": ["ramp-proof-2:9270"], "labels": {"run_id": "proof", "step_id": "step-01"}}]
EOF
sleep 6

curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json,sys
d = json.load(sys.stdin)
ramp_targets = [t for t in d['data']['activeTargets'] if t['scrapePool'] == 'jmeter-ramp']
print('jmeter-ramp targets found:', len(ramp_targets))
for t in ramp_targets:
    print(' -', t['labels'].get('instance'), t['health'])
assert len(ramp_targets) == 2, 'expected both throwaway instances to be discovered'
"

wait $PID1 $PID2
rm -f prometheus/targets/proof-1.json prometheus/targets/proof-2.json
sleep 6
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json,sys
d = json.load(sys.stdin)
ramp_targets = [t for t in d['data']['activeTargets'] if t['scrapePool'] == 'jmeter-ramp']
assert len(ramp_targets) == 0, 'target files were deleted but Prometheus still lists them -- refresh_interval not working?'
print('confirmed: removed target files are no longer discovered, no reload needed')
"
```

Expected: `jmeter-ramp targets found: 2`, both `health: up` (or `down` briefly before the first scrape lands — re-check once if so), then `confirmed: removed target files are no longer discovered, no reload needed`.

- [ ] **Step 6: Commit**

```bash
git add prometheus/prometheus.yml prometheus/targets/.gitkeep ramp-profiles/.gitkeep docker-compose.yml
git commit -m "Add Prometheus file-based service discovery for distributed ramp instances"
```

---

### Task 2: REST sustained-load test plan (`ramp.jmx`)

**Files:**
- Create: `test-plans/rest/ramp.jmx`

**Interfaces:**
- Consumes: `rest-target:4005` (unchanged from the base project), `${__groovy(System.getenv(...))}` credential pattern (unchanged).
- Produces: a REST test plan that loops continuously for `${__P(DURATION,60)}` seconds with `${__P(THREADS,1)}` threads, exporting `jmeter_requests_total`/`jmeter_response_time_ms`/`jmeter_failures_total` with labels `label,code` only (no `run_id` — see Global Constraints) — this is what every later task's orchestration code launches and queries against.

- [ ] **Step 1: Give the user exact content for `test-plans/rest/ramp.jmx`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="REST Ramp — Sustained Load" enabled="true">
      <stringProp name="TestPlan.comments">Sustained-load sibling of rest/starter.jmx, for distributed ramp/spike testing. No run_id/step_id inside this file -- both come from the launching instance's Prometheus file-discovery target labels.</stringProp>
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <boolProp name="TestPlan.tearDown_on_shutdown">true</boolProp>
      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">
        <collectionProp name="Arguments.arguments"/>
      </elementProp>
      <stringProp name="TestPlan.user_define_classpath"></stringProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Customer flow" enabled="true">
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">
          <boolProp name="LoopController.continue_forever">true</boolProp>
          <intProp name="LoopController.loops">-1</intProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">${__P(THREADS,1)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">true</boolProp>
        <stringProp name="ThreadGroup.duration">${__P(DURATION,60)}</stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
      </ThreadGroup>
      <hashTree>
        <ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
          <stringProp name="HTTPSampler.domain">rest-target</stringProp>
          <stringProp name="HTTPSampler.port">4005</stringProp>
          <stringProp name="HTTPSampler.protocol">http</stringProp>
          <stringProp name="HTTPSampler.path"></stringProp>
        </ConfigTestElement>
        <hashTree/>
        <AuthManager guiclass="AuthPanelGui" testclass="AuthManager" testname="HTTP Authorization Manager" enabled="true">
          <collectionProp name="AuthManager.auth_list">
            <elementProp name="" elementType="Authorization">
              <stringProp name="Authorization.url">http://rest-target:4005</stringProp>
              <stringProp name="Authorization.username">${__groovy(System.getenv("BOOMI_TEST_TARGET_BASIC_AUTH_USER"))}</stringProp>
              <stringProp name="Authorization.password">${__groovy(System.getenv("BOOMI_TEST_TARGET_BASIC_AUTH_PASS"))}</stringProp>
              <stringProp name="Authorization.mechanism">BASIC_DIGEST</stringProp>
            </elementProp>
          </collectionProp>
          <boolProp name="AuthManager.controlledByThreadGroup">false</boolProp>
        </AuthManager>
        <hashTree/>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="GET Customers List" enabled="true">
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
          <stringProp name="HTTPSampler.domain"></stringProp>
          <stringProp name="HTTPSampler.port"></stringProp>
          <stringProp name="HTTPSampler.protocol"></stringProp>
          <stringProp name="HTTPSampler.path">/customers?limit=5</stringProp>
          <stringProp name="HTTPSampler.method">GET</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree>
          <JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="Extract first customer_id" enabled="true">
            <stringProp name="JSONPostProcessor.referenceNames">extracted_customer_id</stringProp>
            <stringProp name="JSONPostProcessor.jsonPathExprs">$[0].customer_id</stringProp>
            <stringProp name="JSONPostProcessor.match_numbers">1</stringProp>
            <stringProp name="JSONPostProcessor.defaultValues">NOT_FOUND</stringProp>
          </JSONPostProcessor>
          <hashTree/>
        </hashTree>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="GET Customer Detail" enabled="true">
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
          <stringProp name="HTTPSampler.domain"></stringProp>
          <stringProp name="HTTPSampler.port"></stringProp>
          <stringProp name="HTTPSampler.protocol"></stringProp>
          <stringProp name="HTTPSampler.path">/customers/${extracted_customer_id}</stringProp>
          <stringProp name="HTTPSampler.method">GET</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree>
          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Detail request must be 200" enabled="true">
            <collectionProp name="Asserion.test_strings">
              <stringProp name="49586">200</stringProp>
            </collectionProp>
            <stringProp name="Assertion.custom_message">Extractor likely returned NOT_FOUND instead of a real customer_id.</stringProp>
            <stringProp name="Assertion.test_field">Assertion.response_code</stringProp>
            <boolProp name="Assertion.assume_success">false</boolProp>
            <intProp name="Assertion.test_type">8</intProp>
          </ResponseAssertion>
          <hashTree/>
          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Detail body must echo the extracted id" enabled="true">
            <collectionProp name="Asserion.test_strings">
              <stringProp name="49589">"customer_id":"${extracted_customer_id}"</stringProp>
            </collectionProp>
            <stringProp name="Assertion.custom_message">Response body did not contain the extracted customer_id.</stringProp>
            <stringProp name="Assertion.test_field">Assertion.response_data</stringProp>
            <boolProp name="Assertion.assume_success">false</boolProp>
            <intProp name="Assertion.test_type">16</intProp>
          </ResponseAssertion>
          <hashTree/>
        </hashTree>
        <com.github.johrstrom.listener.PrometheusListener guiclass="com.github.johrstrom.listener.gui.PrometheusListenerGui" testclass="com.github.johrstrom.listener.PrometheusListener" testname="Prometheus Listener" enabled="true">
          <collectionProp name="prometheus.collector_definitions">
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">total JMeter requests</stringProp>
              <stringProp name="collector.metric_name">jmeter_requests_total</stringProp>
              <stringProp name="collector.type">COUNTER</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">CountTotal</stringProp>
            </elementProp>
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">JMeter response time in milliseconds</stringProp>
              <stringProp name="collector.metric_name">jmeter_response_time_ms</stringProp>
              <stringProp name="collector.type">HISTOGRAM</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="collector.quantiles_or_buckets">50,100,250,500,1000,2500,5000,10000</stringProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">ResponseTime</stringProp>
            </elementProp>
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">total JMeter sample failures</stringProp>
              <stringProp name="collector.metric_name">jmeter_failures_total</stringProp>
              <stringProp name="collector.type">COUNTER</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">FailureTotal</stringProp>
            </elementProp>
          </collectionProp>
        </com.github.johrstrom.listener.PrometheusListener>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

Ask the user to save this as `test-plans/rest/ramp.jmx`.

- [ ] **Step 2: Validate it loads and loops for roughly its configured duration, not instantly and not forever**

```bash
time docker-compose run --rm --use-aliases jmeter -n -t /test-plans/rest/ramp.jmx -JTHREADS=3 -JDURATION=20 -l /test-plans/results/ramp-rest-check.jtl 2>&1 | tail -6
```

Expected: the JMeter summary line shows more than 2 total requests (it loops continuously, unlike the single-shot starter), `Err: 0 (0.00%)`, and the `time` command reports roughly 20-23 seconds wall-clock (not ~1s, not indefinite).

- [ ] **Step 3: Confirm the Prometheus Listener's labels really don't include `run_id` (Global Constraints)**

```bash
grep -c '"3">run_id' test-plans/rest/ramp.jmx || echo "confirmed: no run_id label in this file's collectors"
```

Expected: `confirmed: no run_id label in this file's collectors` (the `grep -c` finds nothing, so the `||` branch runs).

- [ ] **Step 4: Commit**

```bash
git add test-plans/rest/ramp.jmx
rm -f test-plans/results/ramp-rest-check.jtl
git commit -m "Add REST sustained-load test plan for distributed ramp/spike testing"
```

---

### Task 3: GraphQL sustained-load test plan (`ramp.jmx`)

**Files:**
- Create: `test-plans/graphql/ramp.jmx`

**Interfaces:**
- Consumes: `graphql-target:8001` (unchanged).
- Produces: the GraphQL sibling of Task 2's REST ramp plan, same sustained-load Thread Group shape.

- [ ] **Step 1: Give the user exact content for `test-plans/graphql/ramp.jmx`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="GraphQL Ramp — Sustained Load" enabled="true">
      <stringProp name="TestPlan.comments">Sustained-load sibling of graphql/starter.jmx, for distributed ramp/spike testing. No run_id/step_id inside this file.</stringProp>
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <boolProp name="TestPlan.tearDown_on_shutdown">true</boolProp>
      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">
        <collectionProp name="Arguments.arguments"/>
      </elementProp>
      <stringProp name="TestPlan.user_define_classpath"></stringProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Customer flow" enabled="true">
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">
          <boolProp name="LoopController.continue_forever">true</boolProp>
          <intProp name="LoopController.loops">-1</intProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">${__P(THREADS,1)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">true</boolProp>
        <stringProp name="ThreadGroup.duration">${__P(DURATION,60)}</stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
      </ThreadGroup>
      <hashTree>
        <ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
          <stringProp name="HTTPSampler.domain">graphql-target</stringProp>
          <stringProp name="HTTPSampler.port">8001</stringProp>
          <stringProp name="HTTPSampler.protocol">http</stringProp>
          <stringProp name="HTTPSampler.path"></stringProp>
        </ConfigTestElement>
        <hashTree/>
        <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="HTTP Header Manager" enabled="true">
          <collectionProp name="HeaderManager.headers">
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">Content-Type</stringProp>
              <stringProp name="Header.value">application/json</stringProp>
            </elementProp>
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">Authorization</stringProp>
              <stringProp name="Header.value">Bearer ${__groovy(System.getenv("BOOMI_TEST_TARGET_API_KEY"))}</stringProp>
            </elementProp>
          </collectionProp>
        </HeaderManager>
        <hashTree/>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="GraphQL Customer List" enabled="true">
          <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments">
              <elementProp name="" elementType="HTTPArgument">
                <boolProp name="HTTPArgument.always_encode">false</boolProp>
                <stringProp name="Argument.value">{"query": "query { customers(limit: 5) { customerId } }"}</stringProp>
                <stringProp name="Argument.metadata">=</stringProp>
              </elementProp>
            </collectionProp>
          </elementProp>
          <stringProp name="HTTPSampler.domain"></stringProp>
          <stringProp name="HTTPSampler.port"></stringProp>
          <stringProp name="HTTPSampler.protocol"></stringProp>
          <stringProp name="HTTPSampler.path">/graphql</stringProp>
          <stringProp name="HTTPSampler.method">POST</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree>
          <JSONPostProcessor guiclass="JSONPostProcessorGui" testclass="JSONPostProcessor" testname="Extract first customerId" enabled="true">
            <stringProp name="JSONPostProcessor.referenceNames">extracted_customer_id</stringProp>
            <stringProp name="JSONPostProcessor.jsonPathExprs">$.data.customers[0].customerId</stringProp>
            <stringProp name="JSONPostProcessor.match_numbers">1</stringProp>
            <stringProp name="JSONPostProcessor.defaultValues">NOT_FOUND</stringProp>
          </JSONPostProcessor>
          <hashTree/>
        </hashTree>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="GraphQL Customer Detail" enabled="true">
          <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments">
              <elementProp name="" elementType="HTTPArgument">
                <boolProp name="HTTPArgument.always_encode">false</boolProp>
                <stringProp name="Argument.value">{"query": "query($id: String!) { customer(customerId: $id) { customerId firstName lastName email } }", "variables": {"id": "${extracted_customer_id}"}}</stringProp>
                <stringProp name="Argument.metadata">=</stringProp>
              </elementProp>
            </collectionProp>
          </elementProp>
          <stringProp name="HTTPSampler.domain"></stringProp>
          <stringProp name="HTTPSampler.port"></stringProp>
          <stringProp name="HTTPSampler.protocol"></stringProp>
          <stringProp name="HTTPSampler.path">/graphql</stringProp>
          <stringProp name="HTTPSampler.method">POST</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree>
          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Detail body must echo the extracted id" enabled="true">
            <collectionProp name="Asserion.test_strings">
              <stringProp name="49587">"customerId":"${extracted_customer_id}"</stringProp>
            </collectionProp>
            <stringProp name="Assertion.custom_message">Response did not contain the extracted customerId.</stringProp>
            <stringProp name="Assertion.test_field">Assertion.response_data</stringProp>
            <boolProp name="Assertion.assume_success">false</boolProp>
            <intProp name="Assertion.test_type">16</intProp>
          </ResponseAssertion>
          <hashTree/>
        </hashTree>
        <com.github.johrstrom.listener.PrometheusListener guiclass="com.github.johrstrom.listener.gui.PrometheusListenerGui" testclass="com.github.johrstrom.listener.PrometheusListener" testname="Prometheus Listener" enabled="true">
          <collectionProp name="prometheus.collector_definitions">
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">total JMeter requests</stringProp>
              <stringProp name="collector.metric_name">jmeter_requests_total</stringProp>
              <stringProp name="collector.type">COUNTER</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">CountTotal</stringProp>
            </elementProp>
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">JMeter response time in milliseconds</stringProp>
              <stringProp name="collector.metric_name">jmeter_response_time_ms</stringProp>
              <stringProp name="collector.type">HISTOGRAM</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="collector.quantiles_or_buckets">50,100,250,500,1000,2500,5000,10000</stringProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">ResponseTime</stringProp>
            </elementProp>
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">total JMeter sample failures</stringProp>
              <stringProp name="collector.metric_name">jmeter_failures_total</stringProp>
              <stringProp name="collector.type">COUNTER</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">FailureTotal</stringProp>
            </elementProp>
          </collectionProp>
        </com.github.johrstrom.listener.PrometheusListener>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

Ask the user to save this as `test-plans/graphql/ramp.jmx`.

- [ ] **Step 2: Validate it loads and loops for roughly its configured duration**

```bash
time docker-compose run --rm --use-aliases jmeter -n -t /test-plans/graphql/ramp.jmx -JTHREADS=3 -JDURATION=20 -l /test-plans/results/ramp-graphql-check.jtl 2>&1 | tail -6
```

Expected: more than 2 total requests, `Err: 0 (0.00%)`, roughly 20-23s wall-clock.

- [ ] **Step 3: Commit**

```bash
git add test-plans/graphql/ramp.jmx
rm -f test-plans/results/ramp-graphql-check.jtl
git commit -m "Add GraphQL sustained-load test plan for distributed ramp/spike testing"
```

---

### Task 4: SOAP sustained-load test plan (`ramp.jmx`)

**Files:**
- Create: `test-plans/soap/ramp.jmx`

**Interfaces:**
- Consumes: the public `www.dneonline.com/calculator.asmx` demo (unchanged).
- Produces: the SOAP sibling of the other two ramp variants.

- [ ] **Step 1: Give the user exact content for `test-plans/soap/ramp.jmx`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="SOAP Ramp — Sustained Load" enabled="true">
      <stringProp name="TestPlan.comments">Sustained-load sibling of soap/smoke-test.jmx, for distributed ramp/spike testing against the public calculator demo. No run_id/step_id inside this file.</stringProp>
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <boolProp name="TestPlan.tearDown_on_shutdown">true</boolProp>
      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">
        <collectionProp name="Arguments.arguments"/>
      </elementProp>
      <stringProp name="TestPlan.user_define_classpath"></stringProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Calculator flow" enabled="true">
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">
          <boolProp name="LoopController.continue_forever">true</boolProp>
          <intProp name="LoopController.loops">-1</intProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">${__P(THREADS,1)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">true</boolProp>
        <stringProp name="ThreadGroup.duration">${__P(DURATION,60)}</stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
      </ThreadGroup>
      <hashTree>
        <ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments"/>
          </elementProp>
          <stringProp name="HTTPSampler.domain">www.dneonline.com</stringProp>
          <stringProp name="HTTPSampler.port">80</stringProp>
          <stringProp name="HTTPSampler.protocol">http</stringProp>
          <stringProp name="HTTPSampler.path"></stringProp>
        </ConfigTestElement>
        <hashTree/>
        <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="HTTP Header Manager" enabled="true">
          <collectionProp name="HeaderManager.headers">
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">Content-Type</stringProp>
              <stringProp name="Header.value">text/xml; charset=utf-8</stringProp>
            </elementProp>
            <elementProp name="" elementType="Header">
              <stringProp name="Header.name">SOAPAction</stringProp>
              <stringProp name="Header.value">&quot;http://tempuri.org/Add&quot;</stringProp>
            </elementProp>
          </collectionProp>
        </HeaderManager>
        <hashTree/>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="SOAP Add 5+7" enabled="true">
          <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments">
              <elementProp name="" elementType="HTTPArgument">
                <boolProp name="HTTPArgument.always_encode">false</boolProp>
                <stringProp name="Argument.value"><![CDATA[<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Add xmlns="http://tempuri.org/">
      <intA>5</intA>
      <intB>7</intB>
    </Add>
  </soap:Body>
</soap:Envelope>]]></stringProp>
                <stringProp name="Argument.metadata">=</stringProp>
              </elementProp>
            </collectionProp>
          </elementProp>
          <stringProp name="HTTPSampler.domain"></stringProp>
          <stringProp name="HTTPSampler.port"></stringProp>
          <stringProp name="HTTPSampler.protocol"></stringProp>
          <stringProp name="HTTPSampler.path">/calculator.asmx</stringProp>
          <stringProp name="HTTPSampler.method">POST</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree>
          <RegexExtractor guiclass="RegexExtractorGui" testclass="RegexExtractor" testname="Extract AddResult" enabled="true">
            <stringProp name="RegexExtractor.useHeaders">false</stringProp>
            <stringProp name="RegexExtractor.refname">extracted_sum</stringProp>
            <stringProp name="RegexExtractor.regex">&lt;AddResult&gt;(\d+)&lt;/AddResult&gt;</stringProp>
            <stringProp name="RegexExtractor.template">$1$</stringProp>
            <stringProp name="RegexExtractor.default">NOT_FOUND</stringProp>
            <stringProp name="RegexExtractor.match_number">1</stringProp>
          </RegexExtractor>
          <hashTree/>
        </hashTree>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="SOAP Add Extracted+3" enabled="true">
          <boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
          <elementProp name="HTTPsampler.Arguments" elementType="Arguments">
            <collectionProp name="Arguments.arguments">
              <elementProp name="" elementType="HTTPArgument">
                <boolProp name="HTTPArgument.always_encode">false</boolProp>
                <stringProp name="Argument.value"><![CDATA[<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <Add xmlns="http://tempuri.org/">
      <intA>${extracted_sum}</intA>
      <intB>3</intB>
    </Add>
  </soap:Body>
</soap:Envelope>]]></stringProp>
                <stringProp name="Argument.metadata">=</stringProp>
              </elementProp>
            </collectionProp>
          </elementProp>
          <stringProp name="HTTPSampler.domain"></stringProp>
          <stringProp name="HTTPSampler.port"></stringProp>
          <stringProp name="HTTPSampler.protocol"></stringProp>
          <stringProp name="HTTPSampler.path">/calculator.asmx</stringProp>
          <stringProp name="HTTPSampler.method">POST</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree>
          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Final sum must be exactly 15" enabled="true">
            <collectionProp name="Asserion.test_strings">
              <stringProp name="49588">&lt;AddResult&gt;15&lt;/AddResult&gt;</stringProp>
            </collectionProp>
            <stringProp name="Assertion.custom_message">Expected AddResult 15 -- the Regex Extractor likely returned NOT_FOUND.</stringProp>
            <stringProp name="Assertion.test_field">Assertion.response_data</stringProp>
            <boolProp name="Assertion.assume_success">false</boolProp>
            <intProp name="Assertion.test_type">16</intProp>
          </ResponseAssertion>
          <hashTree/>
        </hashTree>
        <com.github.johrstrom.listener.PrometheusListener guiclass="com.github.johrstrom.listener.gui.PrometheusListenerGui" testclass="com.github.johrstrom.listener.PrometheusListener" testname="Prometheus Listener" enabled="true">
          <collectionProp name="prometheus.collector_definitions">
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">total JMeter requests</stringProp>
              <stringProp name="collector.metric_name">jmeter_requests_total</stringProp>
              <stringProp name="collector.type">COUNTER</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">CountTotal</stringProp>
            </elementProp>
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">JMeter response time in milliseconds</stringProp>
              <stringProp name="collector.metric_name">jmeter_response_time_ms</stringProp>
              <stringProp name="collector.type">HISTOGRAM</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="collector.quantiles_or_buckets">50,100,250,500,1000,2500,5000,10000</stringProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">ResponseTime</stringProp>
            </elementProp>
            <elementProp name="" elementType="com.github.johrstrom.listener.ListenerCollectorConfig">
              <stringProp name="collector.help">total JMeter sample failures</stringProp>
              <stringProp name="collector.metric_name">jmeter_failures_total</stringProp>
              <stringProp name="collector.type">COUNTER</stringProp>
              <collectionProp name="collector.labels">
                <stringProp name="1">label</stringProp>
                <stringProp name="2">code</stringProp>
              </collectionProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">FailureTotal</stringProp>
            </elementProp>
          </collectionProp>
        </com.github.johrstrom.listener.PrometheusListener>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

Ask the user to save this as `test-plans/soap/ramp.jmx`.

- [ ] **Step 2: Validate it loads and loops for roughly its configured duration**

```bash
time docker-compose run --rm --use-aliases jmeter -n -t /test-plans/soap/ramp.jmx -JTHREADS=2 -JDURATION=20 -l /test-plans/results/ramp-soap-check.jtl 2>&1 | tail -6
```

Expected: more than 2 total requests, `Err: 0 (0.00%)` (depends on the public demo being up), roughly 20-23s wall-clock.

- [ ] **Step 3: Commit**

```bash
git add test-plans/soap/ramp.jmx
rm -f test-plans/results/ramp-soap-check.jtl
git commit -m "Add SOAP sustained-load test plan for distributed ramp/spike testing"
```

---

### Task 5: `ramp.py` core — profile loading, validation, single-instance launch

**Files:**
- Create: `requirements-ramp.txt`
- Create: `ramp.py`
- Create: `ramp.sh`
- Create: `ramp-profiles/rest-minimal.yaml` (a tiny 1-step profile used only to prove this task's mechanism)

**Interfaces:**
- Produces: `load_profile(path) -> dict`, `validate_profile(profile) -> list[str]` (empty list = valid), `resolve_step_duration(profile, step) -> int`, `derive_instance_name(run_id, step_id, idx) -> str`, `write_target_file(run_id, step_id, idx, container_name)`, `delete_target_file(run_id, step_id, idx)`, `launch_instance(test_plan, run_id, step_id, idx, threads, duration) -> subprocess.Popen` — every later task builds on these exact names/signatures.

- [ ] **Step 1: Give the user exact content for `requirements-ramp.txt`**

```
PyYAML>=6.0.3
```

- [ ] **Step 2: Set up the venv**

```bash
python3.14 -m venv .venv-ramp
source .venv-ramp/bin/activate
pip install -r requirements-ramp.txt
```

- [ ] **Step 3: Give the user exact content for `ramp-profiles/rest-minimal.yaml`**

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 20
warmup_sec: 5
stop_rule:
  max_error_rate_pct: 2
  max_avg_response_ms: 3000
steps:
  - instances: 1
    threads: 3
```

- [ ] **Step 4: Give the user exact content for `ramp.py`**

```python
#!/usr/bin/env python3
"""Distributed ramp/spike load orchestrator for the Boomi performance testing stack."""
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent
TARGETS_DIR = REPO_ROOT / "prometheus" / "targets"
RESULTS_DIR = REPO_ROOT / "test-plans" / "results"


def load_profile(path):
    with open(path) as f:
        return yaml.safe_load(f)


def validate_profile(profile):
    """Returns a list of human-readable problems. Empty list means valid."""
    errors = []
    if not profile.get("test_plan"):
        errors.append("missing required key: test_plan")
    if not profile.get("steps"):
        errors.append("steps list is missing or empty")
    else:
        for i, step in enumerate(profile["steps"]):
            label = f"steps[{i}]"
            if "instances" not in step:
                errors.append(f"{label}: missing 'instances'")
            elif not isinstance(step["instances"], int) or step["instances"] < 1:
                errors.append(f"{label}: 'instances' must be a positive integer, got {step.get('instances')!r}")
            if "threads" not in step:
                errors.append(f"{label}: missing 'threads'")
            elif not isinstance(step["threads"], int) or step["threads"] < 1:
                errors.append(f"{label}: 'threads' must be a positive integer, got {step.get('threads')!r}")
            if "duration_sec" in step and (not isinstance(step["duration_sec"], int) or step["duration_sec"] < 1):
                errors.append(f"{label}: 'duration_sec' must be a positive integer, got {step['duration_sec']!r}")
    if "step_duration_sec" in profile and (not isinstance(profile["step_duration_sec"], int) or profile["step_duration_sec"] < 1):
        errors.append(f"step_duration_sec must be a positive integer, got {profile['step_duration_sec']!r}")
    stop_rule = profile.get("stop_rule", {})
    if "max_error_rate_pct" in stop_rule:
        v = stop_rule["max_error_rate_pct"]
        if not isinstance(v, (int, float)) or not (0 <= v <= 100):
            errors.append(f"stop_rule.max_error_rate_pct must be between 0 and 100, got {v!r}")
    if "max_avg_response_ms" in stop_rule:
        v = stop_rule["max_avg_response_ms"]
        if not isinstance(v, (int, float)) or v <= 0:
            errors.append(f"stop_rule.max_avg_response_ms must be a positive number, got {v!r}")
    return errors


def resolve_step_duration(profile, step):
    return step.get("duration_sec", profile["step_duration_sec"])


def derive_instance_name(run_id, step_id, idx):
    return f"jmeter-ramp-{run_id}-{step_id}-{idx}"


def write_target_file(run_id, step_id, idx, container_name):
    TARGETS_DIR.mkdir(parents=True, exist_ok=True)
    target_path = TARGETS_DIR / f"{run_id}-{step_id}-{idx}.json"
    content = [{
        "targets": [f"{container_name}:9270"],
        "labels": {"run_id": run_id, "step_id": step_id},
    }]
    target_path.write_text(json.dumps(content))
    return target_path


def delete_target_file(run_id, step_id, idx):
    target_path = TARGETS_DIR / f"{run_id}-{step_id}-{idx}.json"
    target_path.unlink(missing_ok=True)


def launch_instance(test_plan, run_id, step_id, idx, threads, duration):
    """Launches one JMeter instance in the background. Returns the Popen handle."""
    container_name = derive_instance_name(run_id, step_id, idx)
    # Defensive: a stale container from a prior interrupted run can occupy
    # this name and make --name fail with a conflict error.
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
    write_target_file(run_id, step_id, idx, container_name)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_file = f"/test-plans/results/{run_id}-{step_id}-{idx}.jtl"
    cmd = [
        "docker-compose", "run", "--rm", "--use-aliases",
        "--name", container_name,
        "jmeter", "-n", "-t", f"/test-plans/{test_plan}",
        "-l", result_file,
        f"-JTHREADS={threads}",
        f"-JDURATION={duration}",
    ]
    return subprocess.Popen(cmd, cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    if len(sys.argv) < 2:
        print("Usage: ramp.py <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(sys.argv[1])
    errors = validate_profile(profile)
    if errors:
        print("Profile is invalid:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    run_id = time.strftime("%Y%m%d-%H%M%S")
    step = profile["steps"][0]
    step_id = "step-01"
    duration = resolve_step_duration(profile, step)
    print(f"Launching 1 instance, run_id={run_id}, step_id={step_id}, threads={step['threads']}, duration={duration}s")

    proc = launch_instance(profile["test_plan"], run_id, step_id, 1, step["threads"], duration)
    proc.wait()
    delete_target_file(run_id, step_id, 1)
    print(f"Done. run_id={run_id}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Give the user exact content for `ramp.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/.venv-ramp/bin/python3" "$SCRIPT_DIR/ramp.py" "$@"
```

- [ ] **Step 6: Make it executable and run the minimal profile end-to-end**

```bash
chmod +x ramp.sh
./ramp.sh ramp-profiles/rest-minimal.yaml
```

Expected: prints the launch message, runs for roughly 20 seconds, prints `Done. run_id=<timestamp>`.

- [ ] **Step 7: Confirm the instance was genuinely discoverable and scraped while it ran (not just that JMeter itself ran cleanly)**

```bash
./ramp.sh ramp-profiles/rest-minimal.yaml &
RAMP_PID=$!
sleep 6
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json,sys
d = json.load(sys.stdin)
ramp_targets = [t for t in d['data']['activeTargets'] if t['scrapePool'] == 'jmeter-ramp']
assert len(ramp_targets) == 1, f'expected exactly 1 ramp target while running, found {len(ramp_targets)}'
print('confirmed: 1 ramp-job target visible while the instance is running, health =', ramp_targets[0]['health'])
"
wait $RAMP_PID
sleep 6
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json,sys
d = json.load(sys.stdin)
ramp_targets = [t for t in d['data']['activeTargets'] if t['scrapePool'] == 'jmeter-ramp']
assert len(ramp_targets) == 0, 'target file should have been deleted after the instance finished'
print('confirmed: target file cleaned up after completion')
"
```

Expected: both assertions pass.

- [ ] **Step 8: Commit**

```bash
git add requirements-ramp.txt ramp.py ramp.sh ramp-profiles/rest-minimal.yaml .gitignore
git commit -m "Add ramp.py core: profile loading, validation, single-instance launch"
```

(If `.venv-ramp/` isn't already covered by an existing gitignore pattern, add `.venv-ramp/` to `.gitignore` before this commit — check with `git status` first.)

---

### Task 6: Parallel multi-instance launch per step

**Files:**
- Modify: `ramp.py`
- Create: `ramp-profiles/rest-two-step.yaml` (a 2-step profile with increasing instance counts, used to prove this task)

**Interfaces:**
- Consumes: `launch_instance`, `derive_instance_name`, `resolve_step_duration` (Task 5).
- Produces: `run_step(profile, run_id, step_idx, step) -> dict` with keys `step_id`, `instances_launched`, `exit_codes` (list of each instance's return code) — later tasks (stop-rule, reporting) consume this dict.

- [ ] **Step 1: Give the user the exact replacement for `ramp.py`'s `main()` function, plus a new `run_step()` function above it**

Find this line in `ramp.py`:

```python
def main():
```

Insert this new function immediately **before** it:

```python
def run_step(profile, run_id, step_idx, step):
    step_id = f"step-{step_idx + 1:02d}"
    duration = resolve_step_duration(profile, step)
    instances = step["instances"]
    threads = step["threads"]
    print(f"[{step_id}] launching {instances} instance(s) x {threads} thread(s), duration={duration}s")

    procs = []
    for idx in range(1, instances + 1):
        procs.append(launch_instance(profile["test_plan"], run_id, step_id, idx, threads, duration))

    exit_codes = [p.wait() for p in procs]
    for idx in range(1, instances + 1):
        delete_target_file(run_id, step_id, idx)

    failed = [i + 1 for i, code in enumerate(exit_codes) if code != 0]
    if failed:
        print(f"[{step_id}] WARNING: instance(s) {failed} exited non-zero: {exit_codes}", file=sys.stderr)

    return {"step_id": step_id, "instances_launched": instances, "exit_codes": exit_codes}
```

Then replace the body of `main()` (everything from `run_id = time.strftime(...)` through the final `print(f"Done. run_id={run_id}")`) with:

```python
    run_id = time.strftime("%Y%m%d-%H%M%S")
    step_results = []
    for step_idx, step in enumerate(profile["steps"]):
        result = run_step(profile, run_id, step_idx, step)
        step_results.append(result)

    print(f"Done. run_id={run_id}")
    for r in step_results:
        status = "OK" if all(c == 0 for c in r["exit_codes"]) else "HAD FAILURES"
        print(f"  {r['step_id']}: {r['instances_launched']} instance(s), exit codes {r['exit_codes']} [{status}]")
```

- [ ] **Step 2: Give the user exact content for `ramp-profiles/rest-two-step.yaml`**

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 20
warmup_sec: 5
stop_rule:
  max_error_rate_pct: 2
  max_avg_response_ms: 3000
steps:
  - instances: 1
    threads: 3
  - instances: 2
    threads: 3
```

- [ ] **Step 3: Run it and confirm multiple simultaneous instances are discovered during step 2**

```bash
./ramp.sh ramp-profiles/rest-two-step.yaml &
RAMP_PID=$!
sleep 25
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json,sys
d = json.load(sys.stdin)
ramp_targets = [t for t in d['data']['activeTargets'] if t['scrapePool'] == 'jmeter-ramp']
print('ramp targets during step 2:', len(ramp_targets))
assert len(ramp_targets) == 2, f'expected 2 simultaneous instances in step 2, found {len(ramp_targets)}'
print('confirmed: 2 parallel instances discovered simultaneously')
"
wait $RAMP_PID
```

Expected: `confirmed: 2 parallel instances discovered simultaneously`, and the final printed summary shows both steps `[OK]`.

- [ ] **Step 4: Confirm a failing sibling is reported, not silently swallowed (Review Focus: one instance crashes while others keep running)**

```bash
cat > ramp-profiles/_tmp-one-bad-instance.yaml <<'EOF'
test_plan: rest/ramp.jmx
step_duration_sec: 15
warmup_sec: 2
stop_rule:
  max_error_rate_pct: 100
  max_avg_response_ms: 100000
steps:
  - instances: 2
    threads: 2
EOF
# Force instance 2 specifically to fail fast by temporarily pointing the
# profile at a test plan path that doesn't exist, run it with just the
# bash/docker commands directly (bypassing ramp.py) is unnecessary -- the
# simplest real reproduction is to run run_step() against a profile whose
# test_plan is valid for instance 1 but inject a bad override for one
# instance. Since launch_instance() takes a single shared test_plan for
# the whole step, the honest way to reproduce "one instance crashes" is to
# corrupt one of its two .jtl result paths' parent directory read-only,
# which makes JMeter itself exit non-zero while its sibling succeeds:
chmod -w test-plans/results
./ramp.sh ramp-profiles/_tmp-one-bad-instance.yaml 2>&1 | tee /tmp/one-bad-instance.log
chmod +w test-plans/results
grep -q "WARNING: instance(s) .* exited non-zero" /tmp/one-bad-instance.log \
  && echo "confirmed: non-zero exit codes are surfaced, not silently swallowed" \
  || echo "FAIL: no warning was printed for a failing instance"
rm ramp-profiles/_tmp-one-bad-instance.yaml /tmp/one-bad-instance.log
```

Expected: `confirmed: non-zero exit codes are surfaced, not silently swallowed` (a read-only `results/` directory makes every instance's `-l` write fail, which reliably forces every instance non-zero here — this proves the WARNING path fires at all, which is the thing under test; a *partial* failure, where only one of several siblings fails, exercises the exact same `exit_codes`-collection code path, so this is sufficient without needing a flakier single-instance-only failure injection).

- [ ] **Step 5: Commit**

```bash
git add ramp.py ramp-profiles/rest-two-step.yaml
git commit -m "Add parallel multi-instance launch per ramp step"
```

---

### Task 7: Prometheus querying + stop-rule evaluation

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Consumes: `run_step`'s returned dict (Task 6).
- Produces: `prom_instant_query(promql, at_unix_time) -> dict` (parsed JSON response), `evaluate_stop_rule(profile, run_id, step_id, step_start, step_end, warmup_sec) -> (should_stop: bool, reason: str | None)` — Task 8's report queries and Task 12's saturation check both build on `prom_instant_query`.

- [ ] **Step 1: Give the user the exact new imports and functions to add to `ramp.py`**

At the top of `ramp.py`, change:

```python
import json
import subprocess
import sys
import time
from pathlib import Path
```

to:

```python
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
```

Then add this function anywhere after the imports (e.g. right after the `RESULTS_DIR` constant):

```python
PROMETHEUS_URL = "http://localhost:9090"


def prom_instant_query(promql, at_unix_time):
    """Instant query evaluated AT a specific past timestamp (not 'now') --
    this avoids Prometheus staleness marking affecting results for targets
    that have since disappeared, which plain 'now' queries are vulnerable to."""
    params = urllib.parse.urlencode({"query": promql, "time": at_unix_time})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def _first_value(prom_response, default=0.0):
    """Pulls the first result's numeric value out of a Prometheus instant
    query response, or `default` if the result vector is empty (no data --
    this must never raise, an empty vector is a normal, expected outcome)."""
    results = prom_response.get("data", {}).get("result", [])
    if not results:
        return default
    return float(results[0]["value"][1])


def evaluate_stop_rule(profile, run_id, step_id, step_start, step_end, warmup_sec):
    stop_rule = profile.get("stop_rule", {})
    if not stop_rule:
        return False, None

    window_start = step_start + warmup_sec
    window_sec = max(step_end - window_start, 1)

    error_rate_pct = 0.0
    total_resp = prom_instant_query(
        f'increase(jmeter_requests_total{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])',
        step_end,
    )
    total_requests = sum(float(r["value"][1]) for r in total_resp.get("data", {}).get("result", []))
    if total_requests > 0:
        fail_resp = prom_instant_query(
            f'increase(jmeter_failures_total{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])',
            step_end,
        )
        total_failures = sum(float(r["value"][1]) for r in fail_resp.get("data", {}).get("result", []))
        error_rate_pct = 100.0 * total_failures / total_requests

    avg_resp = prom_instant_query(
        f'sum(increase(jmeter_response_time_ms_sum{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s])) '
        f'/ sum(increase(jmeter_response_time_ms_count{{run_id="{run_id}", step_id="{step_id}"}}[{window_sec}s]))',
        step_end,
    )
    avg_response_ms = _first_value(avg_resp, default=0.0)

    if "max_error_rate_pct" in stop_rule and error_rate_pct > stop_rule["max_error_rate_pct"]:
        return True, f"error rate {error_rate_pct:.1f}% exceeded max_error_rate_pct={stop_rule['max_error_rate_pct']}"
    if "max_avg_response_ms" in stop_rule and avg_response_ms > stop_rule["max_avg_response_ms"]:
        return True, f"avg response {avg_response_ms:.0f}ms exceeded max_avg_response_ms={stop_rule['max_avg_response_ms']}"
    return False, None
```

Then wire it into the step loop. Replace this part of `main()`:

```python
    run_id = time.strftime("%Y%m%d-%H%M%S")
    step_results = []
    for step_idx, step in enumerate(profile["steps"]):
        result = run_step(profile, run_id, step_idx, step)
        step_results.append(result)
```

with:

```python
    run_id = time.strftime("%Y%m%d-%H%M%S")
    warmup_sec = profile.get("warmup_sec", 0)
    step_results = []
    for step_idx, step in enumerate(profile["steps"]):
        step_start = int(time.time())
        result = run_step(profile, run_id, step_idx, step)
        step_end = int(time.time())
        result["step_start"] = step_start
        result["step_end"] = step_end
        step_results.append(result)

        should_stop, reason = evaluate_stop_rule(profile, run_id, result["step_id"], step_start, step_end, warmup_sec)
        if should_stop:
            print(f"[{result['step_id']}] STOP RULE TRIGGERED: {reason}", file=sys.stderr)
            break
```

- [ ] **Step 2: Write a focused unit test for `evaluate_stop_rule`'s empty-data handling (Review Focus item: a step whose query returns no data must not crash)**

```bash
mkdir -p tests
cat > tests/test_ramp.py <<'EOF'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ramp


def test_first_value_returns_default_on_empty_result():
    empty_response = {"status": "success", "data": {"resultType": "vector", "result": []}}
    assert ramp._first_value(empty_response, default=0.0) == 0.0


def test_first_value_extracts_real_value():
    response = {"status": "success", "data": {"resultType": "vector", "result": [{"value": [12345, "42.5"]}]}}
    assert ramp._first_value(response) == 42.5
EOF
```

- [ ] **Step 3: Run it to verify it fails first (the functions don't exist as importable module-level names until this task's edits are saved)**

```bash
source .venv-ramp/bin/activate
pytest tests/test_ramp.py -v
```

Expected: FAIL (or error) if `ramp.py` hasn't been edited yet — do this step **before** editing `ramp.py` if following strict TDD; since this task's Step 1 content is given alongside this test, run this immediately after Step 1 is actually saved and confirm it passes instead (see Step 4).

- [ ] **Step 4: Run it again after Step 1's edits are saved, confirm it passes**

```bash
pytest tests/test_ramp.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Confirm the stop-rule actually fires end-to-end against a real low threshold**

```bash
cat > /tmp/rest-strict-stop.yaml <<'EOF'
test_plan: rest/ramp.jmx
step_duration_sec: 20
warmup_sec: 2
stop_rule:
  max_error_rate_pct: 2
  max_avg_response_ms: 1
steps:
  - instances: 1
    threads: 3
  - instances: 1
    threads: 3
EOF
cp /tmp/rest-strict-stop.yaml ramp-profiles/_tmp-strict-stop.yaml
./ramp.sh ramp-profiles/_tmp-strict-stop.yaml 2>&1 | tail -10
rm ramp-profiles/_tmp-strict-stop.yaml /tmp/rest-strict-stop.yaml
```

Expected: `[step-01] STOP RULE TRIGGERED: avg response ...ms exceeded max_avg_response_ms=1`, and step 2 never runs (the `break` exits the loop after step 1).

- [ ] **Step 6: Commit**

```bash
git add ramp.py tests/test_ramp.py
git commit -m "Add Prometheus querying and stop-rule evaluation to ramp.py"
```

---

### Task 8: JMeter-native per-label-per-step summary report

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Consumes: `prom_instant_query`, `_first_value` (Task 7), `step_results` list built up in `main()`.
- Produces: `query_report_rows(run_id, step_id, step_start, step_end, warmup_sec) -> list[dict]` (one dict per sampler label, keys matching the Aggregate Report columns), `print_summary_report(all_rows)`.

- [ ] **Step 1: Give the user the exact new function to add to `ramp.py`, right after `evaluate_stop_rule`**

```python
def query_report_rows(run_id, step_id, step_start, step_end, warmup_sec):
    """One row per sampler label seen in this step, in JMeter's own
    Aggregate Report column convention."""
    window_start = step_start + warmup_sec
    window_sec = max(step_end - window_start, 1)

    labels_resp = prom_instant_query(
        f'group by (label) (jmeter_requests_total{{run_id="{run_id}", step_id="{step_id}"}})',
        step_end,
    )
    labels = sorted(r["metric"]["label"] for r in labels_resp.get("data", {}).get("result", []))

    rows = []
    for label in labels:
        sel = f'run_id="{run_id}", step_id="{step_id}", label="{label}"'

        samples = _first_value(prom_instant_query(f'increase(jmeter_requests_total{{{sel}}}[{window_sec}s])', step_end))
        failures = _first_value(prom_instant_query(f'increase(jmeter_failures_total{{{sel}}}[{window_sec}s])', step_end))
        error_pct = (100.0 * failures / samples) if samples > 0 else 0.0
        throughput = samples / window_sec if window_sec > 0 else 0.0

        avg_ms = _first_value(prom_instant_query(
            f'increase(jmeter_response_time_ms_sum{{{sel}}}[{window_sec}s]) / increase(jmeter_response_time_ms_count{{{sel}}}[{window_sec}s])',
            step_end,
        ))
        median_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.50, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))
        p90_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.90, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))
        p95_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.95, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))
        p99_ms = _first_value(prom_instant_query(
            f'histogram_quantile(0.99, sum(increase(jmeter_response_time_ms_bucket{{{sel}}}[{window_sec}s])) by (le))', step_end))

        rows.append({
            "step_id": step_id,
            "label": label,
            "samples": int(samples),
            "avg_ms": avg_ms,
            "median_ms": median_ms,
            "p90_ms": p90_ms,
            "p95_ms": p95_ms,
            "p99_ms": p99_ms,
            "error_pct": error_pct,
            "throughput": throughput,
        })
    return rows


def print_summary_report(all_rows):
    print()
    print("=== Summary Report ===")
    header = f"{'Step':<10} {'Label':<28} {'Samples':>8} {'Avg':>8} {'Median':>8} {'90%':>8} {'95%':>8} {'99%':>8} {'Error%':>8} {'Thrpt':>8}"
    print(header)
    print("-" * len(header))
    for row in all_rows:
        print(
            f"{row['step_id']:<10} {row['label']:<28} {row['samples']:>8} "
            f"{row['avg_ms']:>8.0f} {row['median_ms']:>8.0f} {row['p90_ms']:>8.0f} "
            f"{row['p95_ms']:>8.0f} {row['p99_ms']:>8.0f} {row['error_pct']:>7.1f}% {row['throughput']:>7.2f}/s"
        )
```

Then wire report-row collection into the step loop. Replace:

```python
        result["step_start"] = step_start
        result["step_end"] = step_end
        step_results.append(result)

        should_stop, reason = evaluate_stop_rule(profile, run_id, result["step_id"], step_start, step_end, warmup_sec)
        if should_stop:
            print(f"[{result['step_id']}] STOP RULE TRIGGERED: {reason}", file=sys.stderr)
            break
```

with:

```python
        result["step_start"] = step_start
        result["step_end"] = step_end
        step_results.append(result)

        should_stop, reason = evaluate_stop_rule(profile, run_id, result["step_id"], step_start, step_end, warmup_sec)
        if should_stop:
            print(f"[{result['step_id']}] STOP RULE TRIGGERED: {reason}", file=sys.stderr)
            break

    all_rows = []
    for r in step_results:
        all_rows.extend(query_report_rows(run_id, r["step_id"], r["step_start"], r["step_end"], warmup_sec))
    print_summary_report(all_rows)
```

Note: the `print(f"Done. run_id={run_id}")` block that used to be the last thing in `main()` now needs to move to **before** this new report-printing block (right after the `for step_idx, step in enumerate(...)` loop ends, before `all_rows = []`) — check the current file and move it there if it isn't already in that position.

- [ ] **Step 2: Run the two-step profile from Task 6 again and confirm the report shows real, distinct rows per label**

```bash
./ramp.sh ramp-profiles/rest-two-step.yaml
```

Expected: a `=== Summary Report ===` table with separate rows for `GET Customers List` and `GET Customer Detail`, for each of the 2 steps (4 rows total), with non-zero `Samples`/`Thrpt` and `Avg`/percentile values that look plausible (tens of milliseconds against the bundled sample target).

- [ ] **Step 3: Commit**

```bash
git add ramp.py
git commit -m "Add JMeter-native per-label-per-step summary report"
```

---

### Task 9: Slow-request detection

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Consumes: the `.jtl` files Task 5's `launch_instance` already writes (`test-plans/results/<run_id>-<step_id>-<idx>.jtl`).
- Produces: `scan_slow_requests(run_id, step_id, instances, threshold_ms) -> list[dict]` (keys: `timestamp`, `label`, `elapsed_ms`, `response_code`), `print_slow_requests_table(rows)`.

- [ ] **Step 1: Give the user the exact new function to add to `ramp.py`, right after `print_summary_report`**

```python
import csv


def scan_slow_requests(run_id, step_id, instances, threshold_ms):
    slow = []
    for idx in range(1, instances + 1):
        jtl_path = RESULTS_DIR / f"{run_id}-{step_id}-{idx}.jtl"
        if not jtl_path.exists():
            continue
        with open(jtl_path, newline="") as f:
            for row in csv.DictReader(f):
                elapsed = int(row["elapsed"])
                if elapsed > threshold_ms:
                    slow.append({
                        "step_id": step_id,
                        "timestamp": row["timeStamp"],
                        "label": row["label"],
                        "elapsed_ms": elapsed,
                        "response_code": row["responseCode"],
                    })
    return slow


def print_slow_requests_table(rows, total_requests):
    print()
    print(f"=== Slow Requests (total requests: {total_requests}, slow: {len(rows)}) ===")
    if not rows:
        print("(none)")
        return
    header = f"{'Step':<10} {'Timestamp':<15} {'Label':<28} {'Elapsed (ms)':>14} {'Code':>6}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['step_id']:<10} {row['timestamp']:<15} {row['label']:<28} {row['elapsed_ms']:>14} {row['response_code']:>6}")
```

Move the `import csv` to the top of the file alongside the other imports instead of leaving it inline, if the executor is comfortable doing so (either placement works identically in Python; the top-of-file convention matches the rest of this codebase).

Then wire it into `main()`. Replace:

```python
    all_rows = []
    for r in step_results:
        all_rows.extend(query_report_rows(run_id, r["step_id"], r["step_start"], r["step_end"], warmup_sec))
    print_summary_report(all_rows)
```

with:

```python
    all_rows = []
    all_slow = []
    slow_threshold = profile.get("slow_request_threshold_ms")
    for r in step_results:
        all_rows.extend(query_report_rows(run_id, r["step_id"], r["step_start"], r["step_end"], warmup_sec))
        if slow_threshold:
            all_slow.extend(scan_slow_requests(run_id, r["step_id"], r["instances_launched"], slow_threshold))
    print_summary_report(all_rows)
    if slow_threshold:
        total_requests = sum(row["samples"] for row in all_rows)
        print_slow_requests_table(all_slow, total_requests)
```

- [ ] **Step 2: Give the user exact content for `ramp-profiles/rest-slow-detection.yaml`, with a deliberately very low threshold so normal responses count as "slow" for this proof**

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 15
warmup_sec: 2
slow_request_threshold_ms: 5
stop_rule:
  max_error_rate_pct: 100
  max_avg_response_ms: 100000
steps:
  - instances: 1
    threads: 2
```

- [ ] **Step 3: Run it and confirm the slow-requests table lists real entries with correct, plausible data**

```bash
./ramp.sh ramp-profiles/rest-slow-detection.yaml
```

Expected: a `=== Slow Requests (total requests: N, slow: M) ===` section with `M > 0` rows (a 5ms threshold is low enough that normal requests against the bundled sample target exceed it), each row showing a real timestamp, a label matching one of the two REST samplers, and a plausible `elapsed_ms`.

- [ ] **Step 4: Confirm the feature is a true no-op when `slow_request_threshold_ms` is omitted**

```bash
./ramp.sh ramp-profiles/rest-minimal.yaml
```

Expected: no `=== Slow Requests ===` section appears at all (only the summary report), since `ramp-profiles/rest-minimal.yaml` from Task 5 never set `slow_request_threshold_ms`.

- [ ] **Step 5: Commit**

```bash
git add ramp.py ramp-profiles/rest-slow-detection.yaml
git commit -m "Add individual slow-request detection from .jtl files"
```

---

### Task 10: Graceful cleanup on interrupt

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Produces: a `SIGINT`/`SIGTERM` handler that stops the current step's instances and deletes their target files before exiting.

- [ ] **Step 1: Give the user the exact new imports and signal handler to add to `ramp.py`**

Change:

```python
import csv
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
```

to:

```python
import csv
import json
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
```

Then add this near the top of the file, after the `RESULTS_DIR`/`PROMETHEUS_URL` constants:

```python
_current_step_state = {"run_id": None, "step_id": None, "instances": 0}


def _cleanup_current_step():
    run_id = _current_step_state["run_id"]
    step_id = _current_step_state["step_id"]
    instances = _current_step_state["instances"]
    if not run_id:
        return
    print(f"\nCleaning up {step_id}: stopping {instances} instance(s)...", file=sys.stderr)
    for idx in range(1, instances + 1):
        name = derive_instance_name(run_id, step_id, idx)
        subprocess.run(["docker", "stop", name], capture_output=True)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        delete_target_file(run_id, step_id, idx)


def _handle_interrupt(signum, frame):
    _cleanup_current_step()
    print("Interrupted. Prior steps' results remain in Prometheus; no summary report is printed for an interrupted run.", file=sys.stderr)
    sys.exit(130)


signal.signal(signal.SIGINT, _handle_interrupt)
signal.signal(signal.SIGTERM, _handle_interrupt)
```

Then update `run_step` to record its own state for the handler to see. Change:

```python
def run_step(profile, run_id, step_idx, step):
    step_id = f"step-{step_idx + 1:02d}"
    duration = resolve_step_duration(profile, step)
    instances = step["instances"]
    threads = step["threads"]
    print(f"[{step_id}] launching {instances} instance(s) x {threads} thread(s), duration={duration}s")

    procs = []
```

to:

```python
def run_step(profile, run_id, step_idx, step):
    step_id = f"step-{step_idx + 1:02d}"
    duration = resolve_step_duration(profile, step)
    instances = step["instances"]
    threads = step["threads"]
    print(f"[{step_id}] launching {instances} instance(s) x {threads} thread(s), duration={duration}s")

    _current_step_state["run_id"] = run_id
    _current_step_state["step_id"] = step_id
    _current_step_state["instances"] = instances

    procs = []
```

And right after the `exit_codes = [p.wait() for p in procs]` line (still inside `run_step`), add:

```python
    _current_step_state["run_id"] = None
```

(this clears the handler's cleanup target once the step finishes normally, so a Ctrl+C *between* steps — not during one — has nothing to clean up).

- [ ] **Step 2: Verify interrupt cleanup actually works (Review Focus: a crashed/interrupted step must not leave orphans)**

```bash
./ramp.sh ramp-profiles/rest-two-step.yaml &
RAMP_PID=$!
sleep 8
kill -INT $RAMP_PID
sleep 3
echo "--- containers left running? ---"
docker ps --filter "name=jmeter-ramp-" --format "{{.Names}}"
echo "(no output above = confirmed clean)"
echo "--- target files left behind? ---"
ls prometheus/targets/*.json 2>&1 || echo "(no files = confirmed clean)"
```

Expected: both checks show nothing left behind.

- [ ] **Step 3: Commit**

```bash
git add ramp.py
git commit -m "Add graceful cleanup on SIGINT/SIGTERM"
```

---

### Task 11: Confirmation gate above an instance-count threshold

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Consumes: `sys.argv` parsing (adds `--yes`).
- Produces: a blocking confirmation prompt before any step whose `instances` exceeds `CONFIRMATION_THRESHOLD`, bypassable with `--yes`; fails fast (does not hang) when no TTY is available and `--yes` wasn't passed.

- [ ] **Step 1: Give the user the exact new constant and function to add to `ramp.py`, and the `main()` changes to use it**

Add this constant near the top of the file, alongside `PROMETHEUS_URL`:

```python
CONFIRMATION_THRESHOLD = 4
```

Add this function right after `run_step`:

```python
def confirm_large_step(step, step_id, test_plan, skip_confirmation):
    if step["instances"] <= CONFIRMATION_THRESHOLD:
        return
    message = (
        f"\n{step_id} launches {step['instances']} simultaneous instances "
        f"(> {CONFIRMATION_THRESHOLD}) against test plan '{test_plan}'. "
        f"This generates real load -- make sure that's intentional.\n"
    )
    if skip_confirmation:
        print(message + "Proceeding without prompting (--yes was passed).")
        return
    print(message, end="")
    if not sys.stdin.isatty():
        print("No terminal attached to confirm interactively. Re-run with --yes to proceed unattended.", file=sys.stderr)
        sys.exit(1)
    answer = input("Type 'yes' to proceed: ")
    if answer.strip().lower() != "yes":
        print("Aborted.", file=sys.stderr)
        sys.exit(1)
```

Then update argument parsing and the step loop in `main()`. Change:

```python
def main():
    if len(sys.argv) < 2:
        print("Usage: ramp.py <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(sys.argv[1])
```

to:

```python
def main():
    args = sys.argv[1:]
    skip_confirmation = "--yes" in args
    args = [a for a in args if a != "--yes"]
    if len(args) < 1:
        print("Usage: ramp.py [--yes] <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(args[0])
```

And in the step loop, change:

```python
        step_start = int(time.time())
        result = run_step(profile, run_id, step_idx, step)
```

to:

```python
        step_id_preview = f"step-{step_idx + 1:02d}"
        confirm_large_step(step, step_id_preview, profile["test_plan"], skip_confirmation)
        step_start = int(time.time())
        result = run_step(profile, run_id, step_idx, step)
```

- [ ] **Step 2: Confirm the gate fires above the threshold and is skippable with `--yes`**

```bash
cat > ramp-profiles/_tmp-large-step.yaml <<'EOF'
test_plan: rest/ramp.jmx
step_duration_sec: 5
warmup_sec: 1
stop_rule:
  max_error_rate_pct: 100
  max_avg_response_ms: 100000
steps:
  - instances: 5
    threads: 1
EOF
echo "--- without --yes, no tty (should fail fast, not hang) ---"
./ramp.sh ramp-profiles/_tmp-large-step.yaml < /dev/null; echo "exit code: $?"
echo "--- with --yes, should proceed ---"
timeout 15 ./ramp.sh --yes ramp-profiles/_tmp-large-step.yaml 2>&1 | head -5
rm ramp-profiles/_tmp-large-step.yaml
```

Expected: the first invocation exits non-zero immediately with the "No terminal attached" message (not hanging waiting for input); the second proceeds and starts launching 5 instances.

- [ ] **Step 3: Confirm a step at or below the threshold never prompts**

```bash
./ramp.sh ramp-profiles/rest-minimal.yaml < /dev/null
```

Expected: runs to completion with no confirmation-related output at all (1 instance is below the threshold of 4).

- [ ] **Step 4: Commit**

```bash
git add ramp.py
git commit -m "Add confirmation gate above instance-count threshold"
```

---

### Task 12: Load-generator saturation check

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Consumes: `derive_instance_name` (Task 5).
- Produces: `sample_host_cpu(container_names) -> float` (summed CPU % across the named containers), `host_cpu_cores() -> int`; `run_step`'s returned dict gains a `saturated: bool` key that Task 8's report-wiring surfaces.

- [ ] **Step 1: Give the user the exact new functions to add to `ramp.py`, right after `confirm_large_step`**

```python
def host_cpu_cores():
    result = subprocess.run(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True)
    return int(result.stdout.strip())


def sample_host_cpu(container_names):
    """Sums docker stats CPU% across the given containers. Returns 0.0 for
    any container not currently running (already exited) rather than erroring."""
    total = 0.0
    for name in container_names:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", name],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            continue
        pct_str = result.stdout.strip().rstrip("%")
        try:
            total += float(pct_str)
        except ValueError:
            continue
    return total
```

Then update `run_step` to sample CPU partway through the step and flag saturation. Change:

```python
    procs = []
    for idx in range(1, instances + 1):
        procs.append(launch_instance(profile["test_plan"], run_id, step_id, idx, threads, duration))

    exit_codes = [p.wait() for p in procs]
```

to:

```python
    procs = []
    names = []
    for idx in range(1, instances + 1):
        names.append(derive_instance_name(run_id, step_id, idx))
        procs.append(launch_instance(profile["test_plan"], run_id, step_id, idx, threads, duration))

    time.sleep(min(duration / 2, 10))
    cpu_pct = sample_host_cpu(names)
    cores = host_cpu_cores()
    saturated = cpu_pct > (cores * 100 * 0.85)
    if saturated:
        print(f"[{step_id}] WARNING: load generator CPU usage ({cpu_pct:.0f}% across {len(names)} instance(s), "
              f"host has {cores} cores) looks saturated -- these results may reflect the generator's own "
              f"limits, not the target's.", file=sys.stderr)

    exit_codes = [p.wait() for p in procs]
```

And change the `return` statement at the end of `run_step` from:

```python
    return {"step_id": step_id, "instances_launched": instances, "exit_codes": exit_codes}
```

to:

```python
    return {"step_id": step_id, "instances_launched": instances, "exit_codes": exit_codes, "saturated": saturated}
```

Finally, surface this in the report. Change the line in `print_summary_report`'s loop:

```python
    for row in all_rows:
        print(
            f"{row['step_id']:<10} {row['label']:<28} {row['samples']:>8} "
            f"{row['avg_ms']:>8.0f} {row['median_ms']:>8.0f} {row['p90_ms']:>8.0f} "
            f"{row['p95_ms']:>8.0f} {row['p99_ms']:>8.0f} {row['error_pct']:>7.1f}% {row['throughput']:>7.2f}/s"
        )
```

to:

```python
    for row in all_rows:
        flag = " ⚠ possible generator saturation" if row.get("saturated") else ""
        print(
            f"{row['step_id']:<10} {row['label']:<28} {row['samples']:>8} "
            f"{row['avg_ms']:>8.0f} {row['median_ms']:>8.0f} {row['p90_ms']:>8.0f} "
            f"{row['p95_ms']:>8.0f} {row['p99_ms']:>8.0f} {row['error_pct']:>7.1f}% {row['throughput']:>7.2f}/s{flag}"
        )
```

And thread the per-step `saturated` flag onto each of that step's rows. In `main()`, change:

```python
    for r in step_results:
        all_rows.extend(query_report_rows(run_id, r["step_id"], r["step_start"], r["step_end"], warmup_sec))
        if slow_threshold:
```

to:

```python
    for r in step_results:
        step_rows = query_report_rows(run_id, r["step_id"], r["step_start"], r["step_end"], warmup_sec)
        for row in step_rows:
            row["saturated"] = r["saturated"]
        all_rows.extend(step_rows)
        if slow_threshold:
```

- [ ] **Step 2: Confirm `host_cpu_cores`/`sample_host_cpu` return sane values directly**

```bash
source .venv-ramp/bin/activate
python3 -c "
import ramp
print('cores:', ramp.host_cpu_cores())
print('cpu sample of nonexistent container:', ramp.sample_host_cpu(['definitely-not-a-real-container']))
"
```

Expected: `cores:` prints a positive integer (e.g. `12` on this machine); the nonexistent-container sample prints `0.0` (proves it degrades gracefully instead of erroring).

- [ ] **Step 3: Run a normal, non-saturating step and confirm no false-positive flag appears**

```bash
./ramp.sh ramp-profiles/rest-minimal.yaml
```

Expected: the summary report's one row shows no `⚠ possible generator saturation` suffix.

- [ ] **Step 4: Commit**

```bash
git add ramp.py
git commit -m "Add load-generator CPU saturation check"
```

---

### Task 13: `--validate` mode

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Consumes: `load_profile`, `validate_profile` (Task 5).
- Produces: `ramp.py --validate <profile.yaml>` exits 0 and prints success for a valid profile, exits 1 and lists specific problems for an invalid one, and never launches any containers either way.

- [ ] **Step 1: Give the user the exact `main()` changes to add `--validate` handling**

Change:

```python
def main():
    args = sys.argv[1:]
    skip_confirmation = "--yes" in args
    args = [a for a in args if a != "--yes"]
    if len(args) < 1:
        print("Usage: ramp.py [--yes] <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(args[0])
    errors = validate_profile(profile)
    if errors:
        print("Profile is invalid:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
```

to:

```python
def main():
    args = sys.argv[1:]
    skip_confirmation = "--yes" in args
    validate_only = "--validate" in args
    args = [a for a in args if a not in ("--yes", "--validate")]
    if len(args) < 1:
        print("Usage: ramp.py [--yes] [--validate] <profile.yaml>", file=sys.stderr)
        sys.exit(1)

    profile = load_profile(args[0])
    errors = validate_profile(profile)
    if errors:
        print("Profile is invalid:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    if validate_only:
        print(f"Profile is valid: {len(profile['steps'])} step(s), test_plan={profile['test_plan']!r}")
        sys.exit(0)
```

- [ ] **Step 2: Confirm it catches a real problem without launching anything**

```bash
cat > /tmp/bad-profile.yaml <<'EOF'
test_plan: rest/ramp.jmx
step_duration_sec: 20
steps:
  - instances: -1
    threads: 5
EOF
./ramp.sh --validate /tmp/bad-profile.yaml; echo "exit code: $?"
docker ps --filter "name=jmeter-ramp-" --format "{{.Names}}"
echo "(no container names above = confirmed nothing was launched)"
rm /tmp/bad-profile.yaml
```

Expected: prints `- steps[0]: 'instances' must be a positive integer, got -1`, exit code `1`, and no containers were launched.

- [ ] **Step 3: Confirm an empty `steps` list is also caught (Review Focus: an empty steps list must not silently do nothing or launch anything)**

```bash
cat > /tmp/empty-steps.yaml <<'EOF'
test_plan: rest/ramp.jmx
step_duration_sec: 20
steps: []
EOF
./ramp.sh --validate /tmp/empty-steps.yaml; echo "exit code: $?"
./ramp.sh /tmp/empty-steps.yaml < /dev/null; echo "exit code without --validate: $?"
docker ps --filter "name=jmeter-ramp-" --format "{{.Names}}"
echo "(no container names above = confirmed nothing was launched either way)"
rm /tmp/empty-steps.yaml
```

Expected: both invocations print `- steps list is missing or empty` and exit `1` — including the one *without* `--validate*`, proving the same validation check runs unconditionally before anything is launched, not only when explicitly requested.

- [ ] **Step 4: Confirm it reports success on a valid profile, still without launching anything**

```bash
./ramp.sh --validate ramp-profiles/rest-minimal.yaml; echo "exit code: $?"
docker ps --filter "name=jmeter-ramp-" --format "{{.Names}}"
echo "(no container names above = confirmed nothing was launched)"
```

Expected: `Profile is valid: 1 step(s), test_plan='rest/ramp.jmx'`, exit code `0`, nothing launched.

- [ ] **Step 5: Commit**

```bash
git add ramp.py
git commit -m "Add --validate mode"
```

---

### Task 14: Grafana step annotations

**Files:**
- Modify: `ramp.py`

**Interfaces:**
- Produces: `post_grafana_annotation(text, tags, at_unix_time=None)`, called at each step's start and end.

- [ ] **Step 1: Give the user the exact new constant and function to add to `ramp.py`, right after `PROMETHEUS_URL`**

```python
GRAFANA_URL = "http://localhost:3004"
GRAFANA_AUTH = ("admin", "admin")


def post_grafana_annotation(text, tags, at_unix_time=None):
    if at_unix_time is None:
        at_unix_time = time.time()
    payload = json.dumps({
        "time": int(at_unix_time * 1000),
        "tags": tags,
        "text": text,
    }).encode()
    req = urllib.request.Request(f"{GRAFANA_URL}/api/annotations", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    credentials = f"{GRAFANA_AUTH[0]}:{GRAFANA_AUTH[1]}"
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        print(f"Warning: failed to post Grafana annotation: {e}", file=sys.stderr)
```

Then call it at each step's start and end. Change this part of `run_step`:

```python
    _current_step_state["run_id"] = run_id
    _current_step_state["step_id"] = step_id
    _current_step_state["instances"] = instances

    procs = []
```

to:

```python
    _current_step_state["run_id"] = run_id
    _current_step_state["step_id"] = step_id
    _current_step_state["instances"] = instances

    post_grafana_annotation(f"{step_id} started: {instances} instance(s) x {threads} thread(s)", ["ramp", run_id, step_id])

    procs = []
```

And change:

```python
    exit_codes = [p.wait() for p in procs]
```

to:

```python
    exit_codes = [p.wait() for p in procs]
    post_grafana_annotation(f"{step_id} ended", ["ramp", run_id, step_id])
```

- [ ] **Step 2: Confirm annotations actually land, for a real run**

```bash
./ramp.sh ramp-profiles/rest-two-step.yaml > /tmp/ramp-run.log 2>&1 &
RAMP_PID=$!
sleep 2
RUN_ID_LINE=$(grep -o 'run_id=[0-9-]*' /tmp/ramp-run.log | head -1 || true)
wait $RAMP_PID
RUN_ID=$(grep -o 'Done\. run_id=[0-9-]*' /tmp/ramp-run.log | sed 's/.*run_id=//')
echo "run_id was: $RUN_ID"
curl -s -u admin:admin "http://localhost:3004/api/annotations?tags=${RUN_ID}" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('annotations found:', len(d))
assert len(d) == 4, f'expected 4 (start+end for each of 2 steps), found {len(d)}'
for a in d:
    print(' -', a['text'])
"
rm /tmp/ramp-run.log
```

Expected: `annotations found: 4` (step-01 started/ended, step-02 started/ended).

- [ ] **Step 3: Commit**

```bash
git add ramp.py
git commit -m "Add Grafana step-start/step-end annotations"
```

---

### Task 15: Example profiles + full end-to-end validation

**Files:**
- Create: `ramp-profiles/rest-ramp-example.yaml` (a realistic monotonic ramp)
- Create: `ramp-profiles/rest-spike-example.yaml` (a realistic spike pattern, mirroring the US Foods-style reference shared during spec review)
- Delete (clean up throwaway profiles from earlier tasks no longer needed as permanent examples): none — `rest-minimal.yaml` and `rest-two-step.yaml` are fine to keep as lightweight smoke-test fixtures alongside these two more realistic examples.

**Interfaces:**
- Consumes: everything built in Tasks 1-14.
- Produces: two ready-to-use reference profiles demonstrating the two patterns this whole plan exists to support.

- [ ] **Step 1: Give the user exact content for `ramp-profiles/rest-ramp-example.yaml`**

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 30
warmup_sec: 5
slow_request_threshold_ms: 2000
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

- [ ] **Step 2: Give the user exact content for `ramp-profiles/rest-spike-example.yaml`**

```yaml
test_plan: rest/ramp.jmx
step_duration_sec: 60
warmup_sec: 10
slow_request_threshold_ms: 2000
stop_rule:
  max_error_rate_pct: 5
  max_avg_response_ms: 5000
steps:
  - instances: 1
    threads: 10
    duration_sec: 60
  - instances: 2
    threads: 10
    duration_sec: 30
  - instances: 4
    threads: 10
    duration_sec: 45
  - instances: 8
    threads: 10
    duration_sec: 60
  - instances: 2
    threads: 10
    duration_sec: 30
```

- [ ] **Step 3: Run the ramp example end-to-end with every feature enabled, confirm the complete picture holds together**

```bash
./ramp.sh ramp-profiles/rest-ramp-example.yaml 2>&1 | tee /tmp/ramp-example-run.log
```

Expected: 4 steps run in sequence (unless the stop-rule fires first, which is also an acceptable, correctly-reported outcome), a `=== Summary Report ===` with 2 rows per step that actually ran (8 rows if all 4 completed), a `=== Slow Requests ===` section (present or empty, both acceptable depending on actual latencies against the bundled sample target), and no Python tracebacks anywhere in the output.

- [ ] **Step 4: Confirm the spike example's irregular per-step durations are honored**

```bash
time ./ramp.sh ramp-profiles/rest-spike-example.yaml 2>&1 | grep "launching\|STOP RULE"
```

Expected: five `launching N instance(s) x 10 thread(s), duration=Xs` lines with `X` matching each step's own `duration_sec` (60, 30, 45, 60, 30) exactly, in that order (unless the stop-rule fires first) — confirming per-step overrides, not the top-level default, actually drove each step.

- [ ] **Step 5: Run the full pytest suite one more time to confirm nothing regressed across all the incremental edits**

```bash
source .venv-ramp/bin/activate
pytest tests/test_ramp.py -v
```

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```bash
rm -f /tmp/ramp-example-run.log
git add ramp-profiles/rest-ramp-example.yaml ramp-profiles/rest-spike-example.yaml
git commit -m "Add example ramp and spike profiles, validate full feature set end-to-end"
```

---

### Task 16: Static HTML ramp-profile builder (optional convenience layer)

**Files:**
- Create: `ramp-profile-builder.html`

**Interfaces:**
- Consumes: nothing (fully standalone, no backend).
- Produces: a browser page with form fields for the profile's top-level settings and a repeatable "add step" control, which generates and offers for download the identical YAML format `ramp.py` consumes — never replaces hand-editing the YAML directly.

- [ ] **Step 1: Give the user exact content for `ramp-profile-builder.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ramp Profile Builder</title>
<style>
  body { font: 14px/1.5 -apple-system, sans-serif; max-width: 50rem; margin: 2rem auto; padding: 0 1rem; color: #222; }
  h1 { font-size: 1.4rem; }
  label { display: block; margin-top: 0.8rem; font-weight: 600; }
  input[type=text], input[type=number] { width: 100%; box-sizing: border-box; padding: 0.3rem; }
  fieldset { margin-top: 1.5rem; border: 1px solid #ccc; border-radius: 6px; }
  .step-row { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 0.5rem; align-items: end; margin: 0.5rem 0; }
  .step-row label { margin-top: 0; font-weight: normal; font-size: 0.85rem; }
  button { padding: 0.4rem 0.8rem; cursor: pointer; }
  pre { background: #f5f5f5; padding: 1rem; overflow-x: auto; border-radius: 6px; white-space: pre-wrap; }
</style>
</head>
<body>
<h1>Ramp Profile Builder</h1>
<p>Generates the same YAML <code>ramp.py</code> reads directly. This is a convenience layer, not a replacement -- hand-editing the YAML always works too.</p>

<label>Test plan path <small>(e.g. rest/ramp.jmx)</small></label>
<input type="text" id="testPlan" value="rest/ramp.jmx">

<label>Default step duration (seconds)</label>
<input type="number" id="stepDuration" value="60">

<label>Warm-up (seconds)</label>
<input type="number" id="warmupSec" value="15">

<label>Slow request threshold (ms, optional -- leave blank to disable)</label>
<input type="text" id="slowThreshold" value="">

<label>Max error rate (%)</label>
<input type="number" id="maxErrorRate" value="2">

<label>Max average response time (ms)</label>
<input type="number" id="maxAvgResponse" value="3000">

<fieldset>
  <legend>Steps</legend>
  <div id="steps"></div>
  <button type="button" onclick="addStep()">+ Add step</button>
</fieldset>

<p><button type="button" onclick="generate()">Generate YAML</button></p>
<pre id="output"></pre>

<script>
let stepCount = 0;

function addStep(instances, threads, duration) {
  stepCount++;
  const div = document.createElement('div');
  div.className = 'step-row';
  div.id = 'step-' + stepCount;
  div.innerHTML = `
    <div><label>Instances</label><input type="number" class="s-instances" value="${instances ?? 1}"></div>
    <div><label>Threads</label><input type="number" class="s-threads" value="${threads ?? 5}"></div>
    <div><label>Duration override (sec, optional)</label><input type="text" class="s-duration" value="${duration ?? ''}"></div>
    <div><button type="button" onclick="document.getElementById('step-${stepCount}').remove()">Remove</button></div>
  `;
  document.getElementById('steps').appendChild(div);
}

function escapeYamlScalar(s) {
  return /[:#\[\]{}]/.test(s) ? JSON.stringify(s) : s;
}

function generate() {
  const testPlan = document.getElementById('testPlan').value.trim();
  const stepDuration = document.getElementById('stepDuration').value.trim();
  const warmupSec = document.getElementById('warmupSec').value.trim();
  const slowThreshold = document.getElementById('slowThreshold').value.trim();
  const maxErrorRate = document.getElementById('maxErrorRate').value.trim();
  const maxAvgResponse = document.getElementById('maxAvgResponse').value.trim();

  let yaml = `test_plan: ${escapeYamlScalar(testPlan)}\n`;
  yaml += `step_duration_sec: ${stepDuration}\n`;
  yaml += `warmup_sec: ${warmupSec}\n`;
  if (slowThreshold) {
    yaml += `slow_request_threshold_ms: ${slowThreshold}\n`;
  }
  yaml += `stop_rule:\n`;
  yaml += `  max_error_rate_pct: ${maxErrorRate}\n`;
  yaml += `  max_avg_response_ms: ${maxAvgResponse}\n`;
  yaml += `steps:\n`;

  const rows = document.querySelectorAll('.step-row');
  rows.forEach(row => {
    const instances = row.querySelector('.s-instances').value.trim();
    const threads = row.querySelector('.s-threads').value.trim();
    const duration = row.querySelector('.s-duration').value.trim();
    yaml += `  - instances: ${instances}\n`;
    yaml += `    threads: ${threads}\n`;
    if (duration) {
      yaml += `    duration_sec: ${duration}\n`;
    }
  });

  document.getElementById('output').textContent = yaml;
}

addStep(1, 5);
addStep(2, 5);
</script>
</body>
</html>
```

Ask the user to save this as `ramp-profile-builder.html`.

- [ ] **Step 2: Open it and confirm it generates valid YAML that `ramp.py --validate` accepts**

Open `ramp-profile-builder.html` directly in a browser (`open ramp-profile-builder.html` on macOS), click "Generate YAML", copy the output into a file, and validate it:

```bash
# paste the generated output into /tmp/from-builder.yaml, then:
./ramp.sh --validate /tmp/from-builder.yaml
rm /tmp/from-builder.yaml
```

Expected: `Profile is valid: 2 step(s), test_plan='rest/ramp.jmx'` (matching the two default steps the page pre-populates).

- [ ] **Step 3: Commit**

```bash
git add ramp-profile-builder.html
git commit -m "Add static HTML ramp-profile builder as a convenience layer over hand-editing YAML"
```

---

## What this plan does not do

Per the spec's Non-goals: it does not build a VM-cluster or Kubernetes launcher, a Helm chart, real Boomi target integration, CI/CD, a dedicated Grafana ramp-results dashboard panel, JVM heap tracking for the target system, or a machine-readable (JSON/CSV) export of the summary report. All of these remain explicitly deferred, as decided during brainstorming and spec review.
