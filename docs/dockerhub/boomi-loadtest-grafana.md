# 📊 boomi-loadtest-grafana

**Grafana with the load-testing dashboard already built in.** Start it next to Prometheus and your results are on screen with no clicking and no importing. Part of the [Boomi Load Testing Toolkit](https://github.com/pythonicsshahdev/boomi-load-testing-toolkit).

> 💡 This image has no load generator and no Prometheus. It is the picture window for a stack that has both. The toolkit's `setup.sh` starts all the pieces together.

## ✨ What's inside

- 🔌 **A Prometheus datasource**, pre-wired (uid `prometheus`, URL `http://prometheus:9090`)
- 📈 **A 16-panel dashboard**, "Boomi Performance Testing: JMeter Overview"
- 🧱 Built on `grafana/grafana:latest`

### 🗂️ The dashboard at a glance

| Section | What you see |
|---|---|
| 🎯 **KPI cards** | active threads, total requests, throughput, error rate |
| ⏱️ **Percentiles** | p50, p90, p95 and p99 response time |
| 📉 **Trends** | request rate, errors, response time over the whole run |
| 🔍 **Latency breakdown** | connect time vs server latency vs total response time |
| 🧾 **Endpoint table** | every endpoint side by side, with its own numbers |
| 🐢 **Slowest endpoints** | a ranked bar chart, so the problem child is obvious |
| 🚨 **Errors by status code** | what is failing, and with which code |

🎛️ Filter by **run**, **phase** and the request that starts each iteration, and compare run after run because Prometheus keeps the history.

## 🏁 Quick start

The datasource expects a host named `prometheus` on the same Docker network, so the simplest way is the toolkit's Compose file:

```bash
git clone https://github.com/pythonicsshahdev/boomi-load-testing-toolkit
cd boomi-load-testing-toolkit
./setup.sh
```

Open http://localhost:3004 and sign in with `admin` / `admin`. 🔐 Change the password if you expose the port beyond your own machine.

## ⚠️ Good to know

- Grafana keeps its own database in a volume. If you edit the dashboard files and rebuild the image, **also remove the Grafana data volume**, or Grafana keeps showing the old dashboard.
- Ports in the toolkit bind to `127.0.0.1` by default. Open them up deliberately.

## 🏷️ Tags and platforms

| Tag | Meaning |
|---|---|
| `latest` | the newest release |
| `YYYY-MM-DD` | a pinned release |

🖥️ Multi-arch: `linux/amd64` and `linux/arm64`.

## 🔗 Related images

- 🚀 [`boomi-loadtest-jmeter`](https://hub.docker.com/r/pythonicshahdev/boomi-loadtest-jmeter): the load generator
- 🧪 [`boomi-loadtest-graphql-target`](https://hub.docker.com/r/pythonicshahdev/boomi-loadtest-graphql-target): a sample API to test against

## 📄 Source and license

https://github.com/pythonicsshahdev/boomi-load-testing-toolkit, released under the **MIT** license. Grafana itself is licensed separately by Grafana Labs.
