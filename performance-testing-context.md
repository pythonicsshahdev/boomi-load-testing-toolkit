# Performance Testing for Boomi Components — Context Handoff

This doc exists to seed a **new, separate Claude session** dedicated to performance/load testing of Boomi components. It split off from an observability-focused session (the OTel Fanout Platform, a repo at `~/Documents/OTEL/vector_simplified`) once it became clear load testing is its own effort with its own audience constraints, not a feature of that stack. Paste this whole file into the new session to pick up where this one left off.

## Where this came from

The originating repo (`vector_simplified`, not this new session's concern except as background) is a Docker Compose observability stack for Boomi runtimes: Vector + OTel Collector ingest logs/metrics/traces into OpenSearch, Grafana has 11 dashboards, and a Control Plane UI manages consumer fanout. Most recently it shipped an **API Performance dashboard** — Boomi's OTLP spans don't carry a duration field or structured custom attributes natively, so that work added two OTTL processors: one synthesizes `duration_ms` from span start/end nanos, the other extracts eleven `key=value` pairs (`consumerId`, `correlationId`, `transactionId`, `responseCode`, `httpStatus`, `endPoint`, `scenario`, etc.) out of Notify-shape message text into first-class span attributes.

That extraction mechanism matters here because it's the **only plausible hook for correlating a load-test run with the trace spans that stack already captures** — not an HTTP header Boomi reads automatically, but a value the tested process would need to echo into its own Notify message. Whether that's feasible is unconfirmed (see Open Questions).

## The actual ask

Build a load-testing capability for **Boomi-published APIs** — reachable via API Management, secured with basic auth or an API key — that can hit **REST, SOAP, and GraphQL** endpoints (confirmed: it's not just REST), with results visualized somewhere usable, and metrics/trace correlation with the above mechanism as a non-blocking stretch goal.

## The constraint that reframes everything

**The people who will author and maintain these tests are low-code-friendly Boomi users, not developers by trade** — including the person driving this effort. This surfaced *after* a full design had already been built around a code-first tool, and it matters enough to revisit that choice before building anything real. A GUI-driven, point-and-click test-authoring experience is a much better fit for that audience than hand-written JavaScript.

## What's been explored so far (chronological)

1. **Brainstormed and fully designed a k6-based kit** — a self-contained Docker Compose project (`k6/` — k6 + its own bundled Prometheus + its own Grafana, isolated network, no dependency on the main observability stack), with a ramping load profile, env-driven basic-auth/API-key support, and Docker Hub publishing for the Grafana image only (matching this user's existing publishing conventions — see below). Full spec committed at `vector_simplified/docs/superpowers/specs/2026-10-12-k6-boomi-load-testing-design.md`. **This spec is now in question**, not because it was built wrong, but because it predates the low-code-audience constraint.
2. **Collaboration mode established and still in force for this whole effort:** the user hand-types every actual config file/script themselves. Claude's role is to design, give exact reference content to type, and review/debug — not to use file-writing tools to produce the real implementation.
3. **User raised JMeter** (via interest in "JMeter Studio" by QAInsights). Research clarified JMeter Studio is just a desktop GUI theme plugin (dark mode, icon packs) for JMeter's own test-plan editor — it has no bearing on containerized/headless execution or Grafana integration, but it did prompt a real re-evaluation of k6 vs. JMeter as the underlying engine.
4. **Did an honest side-by-side comparison** (full table worth re-deriving if useful, key points below):
   - k6: tiny container, zero-plugin native Prometheus remote-write output, code-as-test (diffs well in git), but SOAP/GraphQL/correlation support is all hand-rolled JS (manual regex over XML, manual `.json()` path extraction) — **not low-code friendly**.
   - JMeter: heavier JVM-based container, no built-in Prometheus output (needs InfluxDB + Backend Listener, or the third-party `jmeter-prometheus-plugin`), but **native GUI authoring**, built-in JSON/XPath/Regex extractors, CSV data-driven testing, and real distributed load-generation support — much closer to what a non-developer could actually build and maintain.
5. **Agreed to a spike before committing**: build minimal POCs in *both* tools across all three protocols (REST, SOAP, GraphQL), each with a **correlation step** (extract a value from response A, feed it into request B) — since that's exactly what real Boomi flows do, and exactly where the tools differ most. Targets chosen were public test services, not real Boomi endpoints yet:
   - REST: `jsonplaceholder.typicode.com` (GET a post → extract `userId` → GET that user)
   - GraphQL: `countries.trevorblades.com/graphql` (query EU countries → extract a `code` → query that country's detail)
   - SOAP: `www.dneonline.com/calculator.asmx` (Add(5,7) → extract `AddResult` → Add(result,3))
6. **Built a copy-paste-friendly reference page** for running that spike by hand, at `vector_simplified/docs/superpowers/specs/k6-vs-jmeter-spike.html` (a local HTML file — Claude-artifact publishing wasn't available in that session due to an API-key auth mode, so it's a plain file; open it directly in a browser). It has the full k6 script content for all three protocols, plus the exact JMeter GUI click-path (servers, paths, extractor configs, body payloads) with one-click copy buttons/chips throughout.
7. **The spike has not been run yet.** That's the concrete next step for the new session: actually build both POCs by hand, note authoring time/friction per tool per protocol, and report back before deciding anything.

## Open questions to resolve in the new session

- **Engine decision**, now properly weighted toward the low-code constraint: does JMeter's GUI authoring outweigh its heavier container and extra Grafana-integration hop? Or is there a middle path (e.g., someone technical maintains a small library of parameterized JMeter test plans that low-code users just fill in values for, via GUI, never touching code either way)?
- **Where should this live?** Given the explicit feedback that mixing this into the observability repo was "muddying the waters," this probably deserves its own project/repo entirely, separate from `vector_simplified` — worth deciding explicitly rather than defaulting to nesting it under that repo again.
- **Trace/span correlation** (tagging a load-test run so it's identifiable in the existing API Performance dashboard) stays a deferred, non-blocking nice-to-have — contingent on confirming whether the actual Boomi process under test can be made to echo a run identifier into its Notify-shape message. Not yet confirmed either way.
- **Docker Hub publishing scope**, if still wanted: the originating project's convention is to publish only images with real baked-in value (e.g., a Grafana image with dashboards/datasources pre-provisioned), not thin wrappers over stock images. If JMeter ends up the choice, the equivalent "what's actually worth publishing" question needs re-asking — a JMeter image would need whatever plugin (e.g. `jmeter-prometheus-plugin`) baked in to be worth shipping.

## Reusable facts from the originating environment (in case still relevant)

- **Docker Hub account:** `pythonicshahdev`. Always push `:latest` and `:YYYY-MM-DD` together in one `buildx` command, two `-t` flags.
- **This Mac's Docker CLI is quirky:** `docker compose` and `docker buildx` as top-level `docker` subcommands are broken (dangling symlinks to an uninstalled app). Working substitutes: the standalone `docker-compose` binary, and a manually-linked `buildx` plugin using builder `multiarch` (`docker buildx use multiarch` before any multi-arch push).
- **GitHub pushes** need the `pythonicsshahdev` account, not the machine's default.
- **Locally installed already:** JMeter 5.6.3 (via brew) with Java 11 — both fine, no setup needed. k6 is not installed (`brew install k6`).
- **Collaboration mode** (repeating because it's load-bearing): the user types every real file themselves; Claude designs, reviews, and gives exact reference content/commands to type, but doesn't use Write/Edit for the actual deliverable.

## Suggested first move in the new session

Run the k6-vs-JMeter spike (content above, or re-fetch `vector_simplified/docs/superpowers/specs/k6-vs-jmeter-spike.html` if it's still reachable from the new working directory) with the low-code-authoring lens explicitly in mind this time — paying attention not just to whether each tool *works*, but to how it would feel for a non-developer to build and maintain going forward. Report findings, then decide the engine and scope a real spec from there.
