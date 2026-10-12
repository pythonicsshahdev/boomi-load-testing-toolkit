# JMeter + Prometheus + Grafana Performance Testing Stack for Boomi APIs

**Status:** Approved design, pending implementation plan
**Date:** 2026-10-12

## Context

This project builds a load-testing capability for **Boomi-published APIs** (reachable via API Management, secured with basic auth or an API key), covering **REST, SOAP, and GraphQL**. Full origin story and prior exploration live in `performance-testing-context.md` at this repo's root — summarized here:

- The effort split off from an observability-focused session (the OTel Fanout Platform, `~/Documents/OTEL/vector_simplified`) because load testing is its own concern with its own audience, not a feature of that stack.
- A full **k6**-based design was built first, then a late-surfacing constraint reframed the whole effort: **the people who will author and maintain these tests are low-code-friendly Boomi users, not developers by trade.** k6's SOAP/GraphQL/correlation support is all hand-rolled JavaScript — not a fit for that audience.
- A k6-vs-JMeter comparison concluded JMeter's native GUI authoring, built-in JSON/XPath/Regex extractors, and CSV data-driven testing are a much closer fit. **The engine decision is JMeter.** k6 and the originally-planned spike are parked, not deleted, in case ever revisited.
- Prometheus was chosen over InfluxDB as the metrics backend specifically for its Kubernetes-native fit (pull-based scraping, ServiceMonitor discovery), since Kubernetes is an explicit eventual deployment target.

## Goals

Build a **general-purpose performance-testing harness** — not a single-example proof — that:

1. Lets a low-code Boomi user author and maintain JMeter test plans for REST, SOAP, and GraphQL targets entirely through JMeter's GUI, including cross-request correlation (extract a value from response A, feed it into request B).
2. Exports live and historical metrics to Prometheus, visualized in pre-built Grafana dashboards, organized so individual test runs can be compared against each other (not one blended timeseries) — this stack is for **reporting and operational insight**, not just live monitoring during a single run.
3. Ships as container images published to Docker Hub, deployable identically via `docker`/`docker-compose` on macOS, Windows, or any VM (e.g. EC2), with a custom Helm chart to follow later for Kubernetes, reusing the same images.
4. Is self-contained and runnable offline for development/demo purposes, bundling its own REST and GraphQL "system under test" sample services alongside the harness.

## Non-goals (explicitly deferred)

- **A bespoke SOAP sample target service.** SOAP is legacy and the harness's SOAP *capability* (JMeter can author/execute SOAP requests regardless of target) doesn't depend on owning a sample server. The public `www.dneonline.com/calculator.asmx` demo service is used instead — no build, no publish, no maintenance burden.
- **Distributed/parallel load generation.** Not urgent at current scale. If needed later, the design should favor several independent JMeter pods each scraped by Prometheus, rather than JMeter's native RMI master/slave distributed mode (painful in containers/k8s — static pod IPs, firewalled RMI ports). This is a note for whoever designs the Helm chart, not a current requirement.
- **Trace/span correlation** with the existing OTel observability stack (tagging a load-test run so it's identifiable in that stack's API Performance dashboard). Contingent on confirming whether a Boomi process under test can be made to echo a run identifier into its Notify-shape message — unconfirmed, out of scope here.
- **The Helm chart itself.** Only the container images need to be Helm-ready (i.e., not architecturally hostile to k8s); the chart is a later, separate piece of work.
- **A Mashery/Cloud API Management-flavored starter template, and MCP Gateway support.** The harness generalizes to both (see Starter templates section), but neither is built in this round. Mashery is cheap to add later (same JMeter mechanics, different auth convention); MCP Gateway is contingent on confirming whether its transport is plain JSON-over-HTTP or a persistent streaming transport (SSE/WebSocket) that would need real additional work.

## Architecture

```
BOOMI_Performance_Testing_POC/
├── docker-compose.yml
├── jmeter/                      # headless execution image
│   └── Dockerfile               # JMeter + jmeter-prometheus-plugin baked in
├── jmeter-gui/                  # optional noVNC image for people without local JMeter
│   └── Dockerfile
├── test-plans/                  # shared bind-mounted volume — the real deliverable, hand-authored
│   ├── rest/
│   ├── graphql/
│   └── soap/
├── prometheus/
│   └── prometheus.yml           # scrape config pointed at jmeter's exported metrics port
├── grafana/
│   ├── Dockerfile               # dashboards + Prometheus datasource pre-provisioned
│   └── provisioning/
├── sample-services/
│   └── graphql-strawberry/      # new — FastAPI + Strawberry GraphQLRouter
│       └── Dockerfile
├── run-test.sh                  # thin wrapper around `docker compose run jmeter ...`
└── performance-testing-context.md
```

### Components

| Service | Role | Image source |
|---|---|---|
| `jmeter` | Headless test execution; a dedicated **Prometheus Listener** element (from `jmeter-prometheus-plugin`, added via Add → Listener → Prometheus Listener — it's its own listener type, not a generic Backend Listener) exposes a metrics port while running | New — published to Docker Hub |
| `jmeter-gui` | Browser-accessible JMeter GUI (noVNC) for authors without a local JMeter install, with JMeter Studio (QAInsights) theme/icon pack baked in for a friendlier, modernized editing experience than JMeter's default Swing UI | New — published to Docker Hub |
| `prometheus` | Scrapes the `jmeter` container's metrics port, persists to its own TSDB | Stock image + baked-in scrape config |
| `grafana` | Pre-provisioned dashboards + Prometheus datasource | New — published to Docker Hub (matches existing convention of only publishing images with real baked-in value) |
| `rest-target` | Sample REST system-under-test | Existing — reuses `pythonicshahdev/fastapi-commercial-source:latest` unchanged |
| `graphql-target` | Sample GraphQL system-under-test, same resource shapes as `rest-target` (customers, products, payments, etc.), built on the same `fastapi-app-reusable` base pattern via Strawberry's `GraphQLRouter` mounted on FastAPI | New — published to Docker Hub |

SOAP has no service: the SOAP `.jmx` plans under `test-plans/soap/` point at the public `dneonline.com/calculator.asmx`, which sits outside the Compose network entirely.

All services share one isolated Docker network, matching the shape of the earlier k6 design (engine + bundled Prometheus + bundled Grafana, no dependency on the main observability stack).

### Authoring (two supported paths, same underlying files)

The `test-plans/` directory is a single source of truth, bind-mounted so it's reachable from:

1. **Local host JMeter GUI** (already installed via brew for this user) opened directly against the host folder — no container involved for authoring. The repo's setup docs should note JMeter Studio (QAInsights) as an optional local install for anyone who wants the same modernized look as path 2 — it's a theming/icon-pack plugin for JMeter's own editor, with no bearing on containerized/headless execution.
2. **`jmeter-gui` container** (noVNC) for anyone without a local JMeter install — same folder, mounted into the container, reachable via browser, with JMeter Studio pre-installed so this path's first impression isn't JMeter's dated stock UI.

Both options are supported simultaneously because the audience's local environment can't be assumed.

### Starter templates

To give the `jmeter-gui`/local-GUI authoring experience real baked-in value (not just a themed editor), this repo ships hand-authored **starter `.jmx` templates** for REST and GraphQL, checked into `test-plans/rest/` and `test-plans/graphql/` directly — not baked into any container image. (`test-plans/` is a bind-mounted shared volume; content placed at that path inside an image would be masked the moment the host folder is mounted over it, so starter content has to live as real tracked files in the repo, authored the same hand-typed way as everything else per [[feedback-collaboration-mode]].) Each starter template already has an HTTP Authorization Manager / Header Manager wired for Boomi API Management's typical auth conventions (Basic Auth or API-key header) and a JSON extractor shaped for Boomi's typical response/error envelope — so building a new test plan means cloning the starter and swapping in a real endpoint URL, not starting from a blank canvas. SOAP has no starter template, consistent with the decision not to invest further engineering in SOAP-specific tooling.

The underlying mechanics (HTTP sampler + auth manager + extractor) aren't specific to Boomi's own API Management — the same starter pattern applies to any gateway fronting a REST/GraphQL endpoint, including **Mashery/Cloud API Management** (different auth convention — typically an API key as a query param rather than a header, sometimes OAuth2 — but otherwise identical JMeter mechanics). A Mashery-flavored starter template is cheap to add later and is **not** part of this round's scope.

**MCP Gateway is explicitly deferred**, contingent on its transport: if an MCP Gateway endpoint turns out to be plain JSON-over-HTTP (JSON-RPC-style request/response), it needs no new work beyond another starter template. If it relies on a persistent streaming transport (SSE or WebSocket, common for MCP's server-to-client messages), that's a materially different JMeter story — needing its WebSocket plugin or custom sampler work — and shouldn't be assumed in scope without first confirming which shape applies.

### Execution lifecycle

1. **Author** — open a `.jmx` in `test-plans/<protocol>/` via either authoring path. Correlation is built with JMeter's native extractors (JSON Path / XPath / Regex), no code. Metrics export is added the same way, as a Prometheus Listener GUI element — not something hidden in the container.
2. **Execute** — `./run-test.sh rest/scenario.jmx` wraps `docker compose run jmeter -n -t test-plans/rest/scenario.jmx -JRUN_ID=<generated>`, generating a timestamp-based `RUN_ID` automatically and passing it in as a JMeter property (`-J`), read in the test plan via `${__P(RUN_ID,manual-run)}`.
3. **Scrape** — `prometheus` scrapes the `jmeter` container's exposed metrics port on an interval, persisting with normal retention. The Prometheus Listener's metric definitions include `run_id` (resolved from the `-J` property into a JMeter variable) as a label on every exported metric.
4. **Observe** — `grafana`'s pre-provisioned dashboards (datasource already pointed at this `prometheus`) support filtering/grouping by a `run_id` dashboard variable, enabling run-over-run comparison instead of one blended timeseries.
5. **Target** — load lands on `rest-target`, `graphql-target`, or the external SOAP demo service depending on which test plan ran.

### Credentials handling

Test plans reference credentials via JMeter's native **HTTP Authorization Manager** (Basic Auth) or **HTTP Header Manager** (API-key headers) elements — both GUI-driven. Rather than a literal secret typed into those fields (which would land in the committed `.jmx`), the author types a variable reference such as `${__env(BOOMI_API_KEY)}`. The actual value comes from an `.env` file (gitignored, same convention as this repo's own `.env`), passed into the `jmeter` container via Compose's `env_file:`. Secrets never touch git; the author still never writes code — just fills in a GUI field with a token-looking placeholder instead of a literal value.

## Testing & validation

Two different kinds of "testing" apply, since most of this stack is infrastructure rather than application logic:

1. **`graphql-target` is real application code** and gets normal unit tests (pytest) for its resolvers, built against the same resource shapes as `rest-target` so protocol parity holds. This is the one place standard TDD applies during implementation.
2. **Everything else is validated with a smoke-test checklist**, run after first build and re-run whenever a component changes:
   - `docker compose up` — all services reach healthy state.
   - Hit `rest-target /ping`, a trivial GraphQL query, and confirm the public SOAP demo endpoint is reachable — targets must be live before trusting any load-test result against them.
   - Run one minimal "hello world" `.jmx` per protocol (no correlation yet) headlessly; `curl` the plugin's metrics port directly (bypassing Prometheus) to isolate where a failure would be if something's wrong.
   - Confirm Prometheus actually scraped it (query its own `/api/v1/query` for the known metric name).
   - Confirm Grafana's pre-provisioned dashboard shows non-empty data, and that the `run_id` variable distinguishes two separate runs from each other.
   - Run the **correlation** test plan per protocol and confirm the second request genuinely received the extracted value — not just that the test "completed." A broken extractor can silently pass an empty/null value forward while the request still returns 200, so successful completion is not proof the correlation step worked. This is the same "looks green, did nothing useful" failure shape seen elsewhere in Boomi tooling, and exactly the kind of thing a low-code author wouldn't notice without a deliberate check.
   - Confirm both authoring paths round-trip: edit a `.jmx` via local JMeter GUI, confirm the `jmeter-gui` container sees the same change, and vice versa.

## Deployment path

1. **Initial deliverable:** container images (`jmeter`, `jmeter-gui`, `grafana`, `graphql-target`) published to Docker Hub under the existing `pythonicshahdev` account, tagged `:latest` and `:YYYY-MM-DD` together per existing convention. The whole stack runs via `docker-compose` identically on macOS, Windows, or any VM (e.g. EC2) — no code changes required between environments.
2. **Later:** a custom Helm chart wraps the same images for Kubernetes. Existing community JMeter Helm charts generally assume JMeter's RMI master/slave distributed mode; this chart should instead use independent parallel JMeter pods, each scraped by Prometheus, if/when distributed load generation is ever needed (see Non-goals).

## Open items carried forward (not blocking this design)

- Trace/span correlation with the existing OTel observability stack's API Performance dashboard — contingent on an unconfirmed assumption about Boomi Notify-shape messages.
- Exact Helm chart structure and whether/when distributed load generation is actually needed.
- Where real (non-sample) Boomi endpoint test plans will eventually point, once this harness is validated against the bundled sample services.

## Collaboration mode

Per established project convention: the user hand-types every real deliverable file (Dockerfiles, `docker-compose.yml`, `.jmx` test plans, the GraphQL service's Python code, etc.) themselves. Claude's role through implementation is to design, review, and provide exact reference content to type — not to author these files directly via file-writing tools. This spec document is an exception, as planning/design documentation rather than a real implementation deliverable.
