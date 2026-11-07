# Roadmap

What is deliberately not built yet, and what would have to be true to build it. Nothing here is promised.

## Kubernetes and a Helm chart

**Status:** parked. Decided on 2026-11-07 that the complications outweigh the benefit until there is a real Kubernetes audience.

**Why it is more than packaging.** `ramp.py` starts load with `docker-compose run` and removes containers by name, and Prometheus finds those containers through target files the script writes. None of that exists in Kubernetes. A chart needs a second runner that launches JMeter as Kubernetes Jobs, and Prometheus discovery through a `PodMonitor` or pod annotations. A chart of the current Compose file would start but could not run a test.

**Shape of the work, if it is picked up:**
1. Put the runner behind a small interface with two backends, Docker (today's behaviour, unchanged) and Kubernetes. Prove it on a local cluster such as kind or minikube.
2. Build the chart on top: console, Grafana (existing image), optional sample targets, Prometheus only if the cluster has none (otherwise a `ServiceMonitor` and a dashboard ConfigMap). Profiles, test plans and baselines on a persistent volume. Credentials from Secrets. `CONSOLE_TOKEN` as a Secret and an ingress for the console.
3. Use independent JMeter pods that Prometheus scrapes, not JMeter's RMI master/slave mode, which is painful on Kubernetes (static pod addresses, firewalled RMI ports).

**What would justify it:** a team that runs its Boomi runtimes on Kubernetes and wants load generated from inside the cluster, or load beyond what one machine can produce.

**Questions to answer first:** who runs it on Kubernetes, is there a cluster to test on, does the cluster already have Prometheus and Grafana, and is Helm required or would Kustomize do.

## Other candidates, not started

- **Scheduled runs** with a notification on regression. Needs the console process to stay running and a decision on where alerts go.
- **Loading an existing profile into the form builder.** Today the builder only creates new profiles.
- **More OAuth variants:** password and authorization-code grants, private-key JWT client authentication, mutual TLS.
- **More databases** than PostgreSQL (each needs its JDBC driver in the JMeter image).
- **Boomi-side metrics** (runtime CPU and memory, execution records) next to the load numbers.
- **MCP Gateway targets,** if its transport turns out not to be plain HTTP.
- **Run retention:** old run folders and raw results files are never pruned.
