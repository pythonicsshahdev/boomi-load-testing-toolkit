# boomi-loadtest-jmeter

Headless Apache JMeter 5.6.3 with the Prometheus listener already installed, built for the **Boomi Load Testing Toolkit**. Use it to load test any HTTP API (REST, GraphQL, SOAP) or a PostgreSQL database and have live metrics scraped by Prometheus.

## What is baked in

- Apache JMeter **5.6.3** on Java 17 (`eclipse-temurin:17-jre-jammy`)
- [`jmeter-prometheus-plugin`](https://github.com/johrstrom/jmeter-prometheus-plugin) **0.7.1**, configured to serve metrics on `0.0.0.0:9270` (the plugin binds to localhost by default, which makes metrics unreachable from other containers)
- PostgreSQL JDBC driver **42.7.4** for database load tests
- Entrypoint is `jmeter`, working directory `/test-plans`

## Quick start

This image is meant to be driven by the toolkit, which generates test plans from a short YAML profile, runs them, and reports results:

```bash
git clone https://github.com/pythonicsshahdev/BOOMI_Performance_Testing_POC
cd BOOMI_Performance_Testing_POC/POC_DEVELOPMENT
./setup.sh
./ramp.sh ramp-profiles/rest-minimal.yaml
```

Standalone use:

```bash
docker run --rm -v "$PWD/test-plans:/test-plans" -p 9270:9270 \
  pythonicshahdev/boomi-loadtest-jmeter:latest -n -t /test-plans/my-plan.jmx
```

Metrics appear at `http://localhost:9270/metrics` while the test runs.

## Tags

`latest` and a dated tag (`YYYY-MM-DD`) for each release. Multi-arch: `linux/amd64`, `linux/arm64`.

## Source and license

https://github.com/pythonicsshahdev/BOOMI_Performance_Testing_POC (MIT)
