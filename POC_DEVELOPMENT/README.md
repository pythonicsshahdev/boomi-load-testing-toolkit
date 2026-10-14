# Boomi Performance Testing Stack

JMeter + Prometheus + Grafana load-testing stack for Boomi-published REST,
SOAP, and GraphQL APIs. See `docs/superpowers/specs/2026-10-12-jmeter-boomi-performance-stack-design.md`
for the full design.

## Authoring test plans

Two ways to build/edit `.jmx` files under `test-plans/`, same shared folder either way:

1. **Local JMeter GUI** — if you already have JMeter installed (e.g. via
   `brew install jmeter`), you **must** first install the
   [`jmeter-prometheus-plugin`](https://github.com/johrstrom/jmeter-prometheus-plugin)
   jar (same version/source the `jmeter` image uses — see `jmeter/Dockerfile`)
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
2. **Browser-based GUI** — run `docker compose up -d jmeter-gui`, then open
   `http://localhost:6080/vnc.html`. No local JMeter install needed.

## Running a test

```bash
./run-test.sh rest/starter.jmx
```

Generates a `RUN_ID`, runs the test headlessly, and prints the id to filter
by in Grafana (`http://localhost:3004`, dashboard "Boomi Performance Testing
— JMeter Overview").

## Credentials

Copy the `BOOMI_TEST_TARGET_*` variables from `.env.example` into this
repo's `.env` and fill in real values for whatever API you're testing.

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
