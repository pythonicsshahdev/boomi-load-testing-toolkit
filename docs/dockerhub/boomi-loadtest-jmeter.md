# 🚀 boomi-loadtest-jmeter

**Headless Apache JMeter with live Prometheus metrics, ready to run.** The load-generating engine of the [Boomi Load Testing Toolkit](https://github.com/pythonicsshahdev/boomi-load-testing-toolkit). Point it at any HTTP API (REST, GraphQL, SOAP) or a PostgreSQL database and watch latency, throughput and errors stream into Prometheus and Grafana.

> 💡 Most people never run this image by hand. The toolkit generates a test plan from a short YAML profile, starts this image for you, and reports the results.

## ✨ What's inside

| | Component | Version |
|---|---|---|
| ☕ | Java runtime (`eclipse-temurin`) | 17 |
| 🔥 | Apache JMeter | 5.6.3 |
| 📈 | [`jmeter-prometheus-plugin`](https://github.com/johrstrom/jmeter-prometheus-plugin) | 0.7.1 |
| 🐘 | PostgreSQL JDBC driver | 42.7.4 |

**Why use this instead of stock JMeter?**
- 📡 Metrics are exposed on `0.0.0.0:9270`. The plugin binds to localhost by default, which makes metrics unreachable from other containers (a quiet failure that wastes hours).
- 🧩 The Prometheus listener and the database driver are already installed, so test plans that use them open and run straight away.
- 🪶 Entrypoint is `jmeter` and the working directory is `/test-plans`, so it behaves like the command you already know.

## 🏁 Quick start with the toolkit

```bash
git clone https://github.com/pythonicsshahdev/boomi-load-testing-toolkit
cd boomi-load-testing-toolkit
./setup.sh                                  # pulls this image and starts the stack
./ramp.sh profiles/rest-minimal.yaml        # a 20 second test against a bundled sample API
```

Then open Grafana at http://localhost:3004 (`admin` / `admin`) to see the run.

## 🔧 Use it on its own

```bash
docker run --rm -v "$PWD/test-plans:/test-plans" -p 9270:9270 \
  pythonicshahdev/boomi-loadtest-jmeter:latest -n -t /test-plans/my-plan.jmx
```

Metrics are live at `http://localhost:9270/metrics` while the test runs.

## 🏷️ Tags and platforms

| Tag | Meaning |
|---|---|
| `latest` | the newest release |
| `YYYY-MM-DD` | a pinned release, for repeatable setups |

🖥️ Multi-arch: `linux/amd64` and `linux/arm64` (Intel, Apple Silicon, AWS Graviton).

## 🔗 Related images

- 📊 [`boomi-loadtest-grafana`](https://hub.docker.com/r/pythonicshahdev/boomi-loadtest-grafana): the dashboard
- 🧪 [`boomi-loadtest-graphql-target`](https://hub.docker.com/r/pythonicshahdev/boomi-loadtest-graphql-target): a sample API to test against

## 📄 Source and license

Everything is on GitHub: https://github.com/pythonicsshahdev/boomi-load-testing-toolkit. Released under the **MIT** license. Apache JMeter and the Prometheus plugin are licensed separately by their authors.
