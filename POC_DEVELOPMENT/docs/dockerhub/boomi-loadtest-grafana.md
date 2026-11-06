# boomi-loadtest-grafana

Grafana with the **Boomi Load Testing Toolkit** dashboard and Prometheus datasource pre-provisioned. Start it next to Prometheus and the load test results are there with no clicking.

## What is baked in

- Based on `grafana/grafana:latest`
- A Prometheus datasource (uid `prometheus`, URL `http://prometheus:9090`)
- Dashboard **Boomi Performance Testing: JMeter Overview**, 16 panels: KPI cards (active threads, total requests, throughput, error rate), p50/p90/p95/p99, response-time trend, connect time vs latency vs response time, endpoint summary table, slowest endpoints, and errors by status code. Filter by `run_id`, `step_id` and the iteration-starting request.

## Quick start

The datasource expects a host named `prometheus` on the same Docker network, so the easiest way to run it is with the toolkit's Compose file:

```bash
git clone https://github.com/pythonicsshahdev/BOOMI_Performance_Testing_POC
cd BOOMI_Performance_Testing_POC/POC_DEVELOPMENT
./setup.sh
```

Then open http://localhost:3004 (login `admin` / `admin`; change this if you expose the port).

## Notes

- Grafana stores its own database in a volume. If you change the dashboard files and rebuild the image, also remove the Grafana data volume or Grafana keeps the old dashboard.
- The image does not include Prometheus or any load generator.

## Tags

`latest` and a dated tag (`YYYY-MM-DD`). Multi-arch: `linux/amd64`, `linux/arm64`.

## Source and license

https://github.com/pythonicsshahdev/BOOMI_Performance_Testing_POC (MIT). Grafana itself is licensed separately by Grafana Labs.
