# JMeter + Prometheus + Grafana Performance Testing Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **COLLABORATION MODE OVERRIDE — READ BEFORE STARTING ANY TASK:** This project does not follow the normal agentic file-authoring pattern. The user hand-types every real deliverable file themselves (Dockerfiles, `docker-compose.yml`, Python app code, shell scripts, `.jmx` test plans, provisioning configs — everything under each task's **Files: Create/Modify**). The executor's job is to **give the user the exact content for each file, wait for them to confirm it's saved, then verify it** (by reading it back, or by running a build/test command that would fail loudly on a mistake) — never to call Write or Edit on those files directly. The only files the executor may create directly are: this plan document itself, the spec document, trivial empty-directory placeholders (`.gitkeep`), and anything explicitly marked "executor may create directly" in a task below. Verification commands (`docker build`, `docker compose up`, `pytest`, `curl`, etc.) are not file-authoring and the executor runs those normally.

**Goal:** Build a general-purpose JMeter + Prometheus + Grafana performance-testing stack for Boomi-published REST/SOAP/GraphQL APIs, containerized and published to Docker Hub, with bundled REST/GraphQL sample targets and dual GUI/noVNC test-plan authoring paths.

**Architecture:** Six Docker Compose services (headless `jmeter` runner, `jmeter-gui` noVNC authoring container, `prometheus`, `grafana`, `rest-target`, `graphql-target`) sharing one bind-mounted `test-plans/` volume and one isolated Docker network. Metrics flow from a JMeter-native Prometheus Listener element through Prometheus's pull-based scrape into pre-provisioned Grafana dashboards, with every metric labeled by a per-run `run_id` for cross-run comparison. SOAP has no bundled target — test plans point at the public `dneonline.com/calculator.asmx` demo service.

**Tech Stack:** Apache JMeter 5.6.3, `jmeter-prometheus-plugin` 0.7.1, JMeter Studio (QAInsights) v1.0.2, Prometheus (`prom/prometheus:latest`), Grafana (`grafana/grafana:latest`), Python 3.12 + FastAPI + Strawberry GraphQL + Faker (graphql-target), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-12-jmeter-boomi-performance-stack-design.md`

## Global Constraints

- JMeter version is pinned to **5.6.3**, downloaded from `https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-5.6.3.tgz` (verified current stable release).
- `jmeter-prometheus-plugin` is pinned to **0.7.1** — single shaded jar (bundles all dependencies), downloaded from `https://github.com/johrstrom/jmeter-prometheus-plugin/releases/download/0.7.1/jmeter-prometheus-plugin-0.7.1.jar`, placed in `$JMETER_HOME/lib/ext/`. It is **not** a Backend Listener — it is its own listener type, fully-qualified class `com.github.johrstrom.listener.PrometheusListener`, added via Add → Listener → Prometheus Listener in the JMeter GUI.
- **Critical, easy to silently get wrong:** the plugin's HTTP metrics server binds `127.0.0.1` by default (`prometheus.ip` property). Inside a container this makes it unreachable from any other container, including Prometheus, with no error on either side — Prometheus just shows the target as down. `jmeter/jmeter.properties.append` MUST set `prometheus.ip=0.0.0.0`. Default port is `9270` (`prometheus.port`).
- JMeter Studio is pinned to **v1.0.2**, jar `jmeter-studio-oss-1.0.2.jar`, downloaded from `https://github.com/QAInsights/jmeter-studio/releases/download/v1.0.2/jmeter-studio-oss-1.0.2.jar`, placed in `$JMETER_HOME/lib/ext/` alongside the Prometheus plugin jar.
- Prometheus metric naming convention, defined once here and used verbatim everywhere else (starter templates' Listener config, Grafana dashboard PromQL, smoke-test script): a Counter named `jmeter_requests_total` (Measuring: Count Total) and a Histogram named `jmeter_response_time_ms` (Measuring: Response Time, buckets `50,100,250,500,1000,2500,5000,10000`), both with Labels `label,code,run_id` (`label` and `code` are the plugin's own keywords for sampler name and response code; `run_id` is our JMeter variable). The plugin's built-in `jmeter_threads` gauge is used as-is (default name, `prometheus.save.threads=true` is already the default).
- `run_id` is propagated as a JMeter **property** via `-JRUN_ID=<value>` on the CLI, read in every test plan via `${__P(RUN_ID,manual-run)}` — not an OS/container environment variable. This needs no Compose-level environment wiring and defaults sensibly when a test plan is run interactively from a GUI without the flag.
- Real credentials (Basic Auth / API key) for test targets are the one thing that IS an OS environment variable, via `${__env(VAR_NAME)}` sourced from a gitignored `.env` passed into the `jmeter`/`jmeter-gui` services via Compose's `env_file:`. Never type a literal secret into a `.jmx` field.
- Docker Hub account is `pythonicshahdev`. Images this plan publishes (`jmeter`, `jmeter-gui`, `grafana`, `graphql-target`) get tagged `:latest` and `:YYYY-MM-DD` together per existing convention — this plan builds and locally tags them (`:dev`) for development; the actual `docker push` to Docker Hub happens only when the user explicitly asks for it, not automatically at the end of this plan.
- SOAP gets **no** bundled service and **no** Boomi-auth starter template — only a correlation smoke-test plan against the public `www.dneonline.com/calculator.asmx` (verified live: `Add(5,7)` returns `12`).
- Out of scope, do not build: Mashery/CAM starter template, MCP Gateway support, the Helm chart itself, distributed/parallel load generation, trace/span correlation with the OTel observability stack.
- rest-target reuses the existing `pythonicshahdev/fastapi-commercial-source:latest` image unchanged — real, live resource shapes confirmed via its OpenAPI schema: `Customer360` (`customer_id`, `first_name`, `last_name`, `email`, `city`, `country`, `customer_segment`, `status`, plus more) at `GET /customers` and `GET /customers/{customer_id}`; `Product360` similarly at `/products`.

## Review Focus

- **Silent correlation-extractor failure**: an extractor can return its default/empty value while the dependent request still returns HTTP 200, making a broken test plan look like it passed. Every correlation test plan in this project (Tasks 8, 9, 10) must include an assertion that specifically fails when the extracted value didn't propagate — not just "the test completed."
- **Prometheus scrape silently returns nothing because of the 127.0.0.1 bind default** (see Global Constraints) — Task 3's acceptance check must `curl` the plugin's metrics port from a *different* container on the network, not just from inside the `jmeter` container itself, since the latter would succeed even with the bug.
- **Credentials typed as literal secrets instead of `${__env(...)}` references** — nothing stops a user from pasting a real password into the Authorization Manager's password field instead of a variable reference. Task 8's starter template demonstrates the correct pattern explicitly so there's a working example to copy, and the task's review step checks the saved `.jmx` text for the `${__env(` pattern rather than a literal-looking value.
- **`run_id` not actually distinguishing two runs** — if the Prometheus Listener's Labels column were left as just `label,code` (forgetting `run_id`), every run's metrics would blend into one timeseries with no way to compare them, and this would not surface as an error anywhere — it would just make the Grafana `run_id` dropdown show nothing useful. Task 11's final validation explicitly runs the same test plan twice and confirms Grafana's `run_id` variable lists two distinct values.
- **Bind-mount round-trip failure between the two authoring paths** — host-container UID/permission mismatches can let one authoring path write a file the other can't read (or vice versa) without any visible error until someone goes looking for a file that silently isn't there. Task 4's acceptance check explicitly saves a file from one path and confirms its presence and read access from the other, not just that both containers start.

---

### Task 1: Repo scaffolding

**Files:**
- Create: `.env.example`
- Executor may create directly (trivial, no real content): `test-plans/rest/.gitkeep`, `test-plans/graphql/.gitkeep`, `test-plans/soap/.gitkeep`, `test-plans/results/.gitkeep`, `test-plans/_infra-check/.gitkeep`, and the directories `jmeter/`, `jmeter-gui/`, `prometheus/`, `grafana/provisioning/datasources/`, `grafana/provisioning/dashboards/`, `grafana/dashboards/`, `sample-services/graphql-strawberry/app/`, `sample-services/graphql-strawberry/tests/`

**Interfaces:**
- Produces: the directory layout every later task writes into; `.env.example` documents the exact variable names (`BOOMI_TEST_TARGET_API_KEY`, `BOOMI_TEST_TARGET_BASIC_AUTH_USER`, `BOOMI_TEST_TARGET_BASIC_AUTH_PASS`) that Task 8's `.jmx` references via `${__env(...)}`.

- [ ] **Step 1: Create directory scaffolding**

```bash
mkdir -p test-plans/rest test-plans/graphql test-plans/soap test-plans/results test-plans/_infra-check
mkdir -p jmeter jmeter-gui prometheus
mkdir -p grafana/provisioning/datasources grafana/provisioning/dashboards grafana/dashboards
mkdir -p sample-services/graphql-strawberry/app sample-services/graphql-strawberry/tests
touch test-plans/rest/.gitkeep test-plans/graphql/.gitkeep test-plans/soap/.gitkeep test-plans/results/.gitkeep test-plans/_infra-check/.gitkeep
```

- [ ] **Step 2: Give the user exact content for `.env.example`**

```bash
# Credentials for the system(s) under test — used by JMeter's HTTP
# Authorization Manager / HTTP Header Manager elements via ${__env(VAR_NAME)}.
# Copy this file to .env and fill in real values; .env is already gitignored.
# These are NOT the same as this repo's own Boomi platform .env — this is
# for whatever API the *load test itself* is hitting.

BOOMI_TEST_TARGET_API_KEY=
BOOMI_TEST_TARGET_BASIC_AUTH_USER=
BOOMI_TEST_TARGET_BASIC_AUTH_PASS=
```

Ask the user to save this as `.env.example` in the repo root. Note: this repo already has a real `.env` (from the Boomi platform template, with unrelated `BOOMI_API_TOKEN`/etc. credentials for Claude's own platform access) — ask the user to also **append** the three `BOOMI_TEST_TARGET_*` lines above into that existing `.env` file (don't overwrite it), since Task 8's Authorization Manager demo reads them.

- [ ] **Step 3: Verify**

```bash
git status
```

Expected: `.env.example` listed as untracked (new), no other unexpected changes; `.gitignore` already excludes `.env` (confirmed in a prior session — don't re-add it).

- [ ] **Step 4: Commit**

```bash
git add .env.example test-plans jmeter jmeter-gui prometheus grafana sample-services
git commit -m "Scaffold directory structure and .env.example for performance stack"
```

---

### Task 2: `graphql-target` sample service

**Files:**
- Create: `sample-services/graphql-strawberry/requirements.txt`
- Create: `sample-services/graphql-strawberry/requirements-dev.txt`
- Create: `sample-services/graphql-strawberry/app/__init__.py`
- Create: `sample-services/graphql-strawberry/app/data.py`
- Create: `sample-services/graphql-strawberry/app/schema.py`
- Create: `sample-services/graphql-strawberry/app/main.py`
- Create: `sample-services/graphql-strawberry/tests/test_graphql.py`
- Create: `sample-services/graphql-strawberry/Dockerfile`

**Interfaces:**
- Consumes: nothing from other tasks (standalone).
- Produces: a FastAPI app object `app.main:app` listening on port `8001`, with `GET /ping` → `{"status": "ok"}` and `POST /graphql` (Strawberry schema with `customers(limit)`, `customer(customerId)`, `products(limit)`, `product(materialNumber)` queries returning fixture data seeded with a fixed Faker seed `20261012`, 20 customers `CUST-000001`..`CUST-000020` and 20 products `MAT-000001`..`MAT-000020`). Later tasks (6, 9) depend on this port and these exact field/query names.

- [ ] **Step 1: Give the user exact content for `requirements.txt`**

```
fastapi>=0.143.0
uvicorn[standard]>=0.54.0
strawberry-graphql[fastapi]>=0.334.2
faker>=40.43.0
```

- [ ] **Step 2: Give the user exact content for `requirements-dev.txt`**

```
-r requirements.txt
pytest>=9.1.1
httpx>=0.28.1
```

- [ ] **Step 3: Give the user exact content for `app/__init__.py`**

```python
```

(empty file — just makes `app` a package)

- [ ] **Step 4: Give the user exact content for `app/data.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from faker import Faker

_SEED = 20261012
_CUSTOMER_SEGMENTS = ["Enterprise", "Startup", "Government", "SMB"]
_CUSTOMER_STATUSES = ["Active", "Inactive"]
_PRODUCT_STATUSES = ["Active", "Discontinued"]
_MATERIAL_GROUPS = ["MG-SAFTY", "MG-PHARM", "MG-ELECT", "MG-APPRL"]


@dataclass
class Customer:
    customer_id: str
    first_name: str
    last_name: str
    email: str
    city: str
    country: str
    customer_segment: str
    status: str


@dataclass
class Product:
    material_number: str
    material_description: str
    material_group: str
    price: float
    currency: str
    status: str


def _generate_customers(fake: Faker, count: int) -> list[Customer]:
    customers = []
    for i in range(1, count + 1):
        customers.append(
            Customer(
                customer_id=f"CUST-{i:06d}",
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                email=fake.email(),
                city=fake.city(),
                country=fake.country_code(),
                customer_segment=fake.random_element(_CUSTOMER_SEGMENTS),
                status=fake.random_element(_CUSTOMER_STATUSES),
            )
        )
    return customers


def _generate_products(fake: Faker, count: int) -> list[Product]:
    products = []
    for i in range(1, count + 1):
        products.append(
            Product(
                material_number=f"MAT-{i:06d}",
                material_description=fake.catch_phrase(),
                material_group=fake.random_element(_MATERIAL_GROUPS),
                price=round(fake.pyfloat(min_value=5, max_value=500, right_digits=2), 2),
                currency="USD",
                status=fake.random_element(_PRODUCT_STATUSES),
            )
        )
    return products


_fake = Faker()
_fake.seed_instance(_SEED)

CUSTOMERS: list[Customer] = _generate_customers(_fake, 20)
PRODUCTS: list[Product] = _generate_products(_fake, 20)


def find_customer(customer_id: str) -> Customer | None:
    return next((c for c in CUSTOMERS if c.customer_id == customer_id), None)


def find_product(material_number: str) -> Product | None:
    return next((p for p in PRODUCTS if p.material_number == material_number), None)
```

- [ ] **Step 5: Give the user exact content for `app/schema.py`**

```python
from __future__ import annotations

import strawberry

from app.data import CUSTOMERS, PRODUCTS, find_customer, find_product


@strawberry.type
class Customer:
    customer_id: str
    first_name: str
    last_name: str
    email: str
    city: str
    country: str
    customer_segment: str
    status: str


@strawberry.type
class Product:
    material_number: str
    material_description: str
    material_group: str
    price: float
    currency: str
    status: str


def _to_graphql_customer(c) -> Customer:
    return Customer(
        customer_id=c.customer_id,
        first_name=c.first_name,
        last_name=c.last_name,
        email=c.email,
        city=c.city,
        country=c.country,
        customer_segment=c.customer_segment,
        status=c.status,
    )


def _to_graphql_product(p) -> Product:
    return Product(
        material_number=p.material_number,
        material_description=p.material_description,
        material_group=p.material_group,
        price=p.price,
        currency=p.currency,
        status=p.status,
    )


@strawberry.type
class Query:
    @strawberry.field
    def customers(self, limit: int = 10) -> list[Customer]:
        return [_to_graphql_customer(c) for c in CUSTOMERS[:limit]]

    @strawberry.field
    def customer(self, customer_id: str) -> Customer | None:
        found = find_customer(customer_id)
        return _to_graphql_customer(found) if found else None

    @strawberry.field
    def products(self, limit: int = 10) -> list[Product]:
        return [_to_graphql_product(p) for p in PRODUCTS[:limit]]

    @strawberry.field
    def product(self, material_number: str) -> Product | None:
        found = find_product(material_number)
        return _to_graphql_product(found) if found else None


schema = strawberry.Schema(query=Query)
```

- [ ] **Step 6: Give the user exact content for `app/main.py`**

```python
from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from app.schema import schema

app = FastAPI(title="Boomi Performance Testing — GraphQL Sample Target")

graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 7: Give the user exact content for `tests/test_graphql.py`**

```python
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ping():
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_customers_query_returns_fixtures():
    query = """
    query {
        customers(limit: 3) {
            customerId
            firstName
            email
        }
    }
    """
    response = client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    customers = body["data"]["customers"]
    assert len(customers) == 3
    assert customers[0]["customerId"] == "CUST-000001"


def test_customer_query_by_id_matches_list_entry():
    list_query = "query { customers(limit: 1) { customerId } }"
    list_response = client.post("/graphql", json={"query": list_query})
    customer_id = list_response.json()["data"]["customers"][0]["customerId"]

    detail_query = """
    query($id: String!) {
        customer(customerId: $id) {
            customerId
            firstName
            lastName
            email
        }
    }
    """
    detail_response = client.post(
        "/graphql", json={"query": detail_query, "variables": {"id": customer_id}}
    )
    detail = detail_response.json()["data"]["customer"]
    assert detail["customerId"] == customer_id


def test_customer_query_unknown_id_returns_null():
    query = """
    query {
        customer(customerId: "CUST-999999") {
            customerId
        }
    }
    """
    response = client.post("/graphql", json={"query": query})
    body = response.json()
    assert "errors" not in body
    assert body["data"]["customer"] is None


def test_products_query_returns_fixtures():
    query = "query { products(limit: 2) { materialNumber price currency } }"
    response = client.post("/graphql", json={"query": query})
    body = response.json()
    assert "errors" not in body
    assert len(body["data"]["products"]) == 2
```

- [ ] **Step 8: Give the user exact content for `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

- [ ] **Step 9: Run the tests locally**

```bash
cd sample-services/graphql-strawberry
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/ -v
```

Expected: all 5 tests pass. If `test_customer_query_by_id_matches_list_entry` fails, that's the exact correlation pattern (list → extract id → detail) the GraphQL starter `.jmx` in Task 9 will need to replicate — don't proceed to Task 9 until this passes.

- [ ] **Step 10: Build and smoke-test the image**

```bash
cd /Users/shahabdullahmac/Documents/boomicompanion_projects/BOOMI_Performance_Testing_POC
docker build -t boomi-graphql-target:dev ./sample-services/graphql-strawberry
docker run --rm -d -p 8001:8001 --name graphql-target-check boomi-graphql-target:dev
sleep 2
curl -sf http://localhost:8001/ping
curl -sf -X POST http://localhost:8001/graphql -H "Content-Type: application/json" \
  -d '{"query":"query { customers(limit: 1) { customerId } }"}'
docker stop graphql-target-check
```

Expected: `/ping` returns `{"status":"ok"}`; the GraphQL query returns `{"data":{"customers":[{"customerId":"CUST-000001"}]}}` with no `errors` key.

- [ ] **Step 11: Commit**

```bash
cd /Users/shahabdullahmac/Documents/boomicompanion_projects/BOOMI_Performance_Testing_POC
git add sample-services/graphql-strawberry
git commit -m "Add graphql-target sample service (FastAPI + Strawberry)"
```

---

### Task 3: `jmeter` headless image + Prometheus scrape pipeline

**Files:**
- Create: `jmeter/Dockerfile`
- Create: `jmeter/jmeter.properties.append`
- Create: `prometheus/prometheus.yml`
- Create: `docker-compose.yml` (initial version — `jmeter` + `prometheus` services only; later tasks extend it)
- Create: `test-plans/_infra-check/empty.jmx`

**Interfaces:**
- Produces: a `boomi-jmeter:dev` image with JMeter 5.6.3 + the Prometheus plugin on `$PATH` as `jmeter`, exposing port `9270` when a test plan containing a Prometheus Listener is run. Task 4 builds `FROM boomi-jmeter:dev`. Tasks 8/9/10 run against this same image via `docker compose run jmeter`.

- [ ] **Step 1: Give the user exact content for `jmeter/jmeter.properties.append`**

```properties
# Boomi Performance Testing Stack overrides — appended to the stock jmeter.properties.
# Containers need the Prometheus listener to bind on all interfaces, not just
# localhost, or the prometheus service on the shared Docker network can never
# reach it — and that failure is silent on both sides (see Review Focus).
prometheus.ip=0.0.0.0
prometheus.port=9270
```

- [ ] **Step 2: Give the user exact content for `jmeter/Dockerfile`**

```dockerfile
FROM eclipse-temurin:17-jre-jammy

ARG JMETER_VERSION=5.6.3
ARG JMETER_PROMETHEUS_PLUGIN_VERSION=0.7.1
ENV JMETER_HOME=/opt/apache-jmeter-${JMETER_VERSION}
ENV PATH="${JMETER_HOME}/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://dlcdn.apache.org/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz" -o /tmp/jmeter.tgz \
    && tar -xzf /tmp/jmeter.tgz -C /opt \
    && rm /tmp/jmeter.tgz

RUN curl -fsSL "https://github.com/johrstrom/jmeter-prometheus-plugin/releases/download/${JMETER_PROMETHEUS_PLUGIN_VERSION}/jmeter-prometheus-plugin-${JMETER_PROMETHEUS_PLUGIN_VERSION}.jar" \
    -o "${JMETER_HOME}/lib/ext/jmeter-prometheus-plugin-${JMETER_PROMETHEUS_PLUGIN_VERSION}.jar"

COPY jmeter.properties.append /tmp/jmeter.properties.append
RUN cat /tmp/jmeter.properties.append >> "${JMETER_HOME}/bin/jmeter.properties" \
    && rm /tmp/jmeter.properties.append

EXPOSE 9270
WORKDIR /test-plans
ENTRYPOINT ["jmeter"]
CMD ["--help"]
```

- [ ] **Step 3: Give the user exact content for `test-plans/_infra-check/empty.jmx`**

This is a throwaway fixture used only to prove the metrics pipeline works, before any real test plan exists (Tasks 8–10 build those). It has a Thread Group with zero samplers — the Prometheus Listener still exports JVM/thread metrics automatically.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Infra Check — Empty Plan" enabled="true">
      <stringProp name="TestPlan.comments">Throwaway fixture: proves the Prometheus Listener + scrape pipeline works, independent of any real protocol test plan.</stringProp>
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <boolProp name="TestPlan.tearDown_on_shutdown">true</boolProp>
      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">
        <collectionProp name="Arguments.arguments"/>
      </elementProp>
      <stringProp name="TestPlan.user_define_classpath"></stringProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="idle" enabled="true">
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">
          <boolProp name="LoopController.continue_forever">false</boolProp>
          <intProp name="LoopController.loops">1</intProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">1</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">false</boolProp>
        <stringProp name="ThreadGroup.duration"></stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
      </ThreadGroup>
      <hashTree>
        <com.github.johrstrom.listener.PrometheusListener guiclass="com.github.johrstrom.listener.gui.PrometheusListenerGui" testclass="com.github.johrstrom.listener.PrometheusListener" testname="Prometheus Listener" enabled="true">
          <collectionProp name="prometheus.collector_definitions"/>
        </com.github.johrstrom.listener.PrometheusListener>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

Ask the user to save this exact content as `test-plans/_infra-check/empty.jmx`.

- [ ] **Step 4: Give the user exact content for `prometheus/prometheus.yml`**

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 5s

scrape_configs:
  - job_name: "jmeter"
    static_configs:
      - targets: ["jmeter:9270"]
```

- [ ] **Step 5: Give the user exact content for `docker-compose.yml`**

```yaml
networks:
  perf-test-net:
    driver: bridge

volumes:
  prometheus-data:

services:
  jmeter:
    build:
      context: ./jmeter
    image: boomi-jmeter:dev
    networks:
      - perf-test-net
    volumes:
      - ./test-plans:/test-plans
    env_file:
      - .env
    profiles: ["tools"]

  prometheus:
    image: prom/prometheus:latest
    networks:
      - perf-test-net
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
```

Note: `jmeter` has `profiles: ["tools"]` so it does *not* start with a plain `docker compose up` — it only runs on demand via `docker compose run jmeter ...`, since it's a one-shot test executor, not a daemon.

- [ ] **Step 6: Build the jmeter image**

```bash
docker build -t boomi-jmeter:dev ./jmeter
```

Expected: builds successfully; confirms JMeter 5.6.3 downloads, the plugin jar downloads, and `jmeter.properties` gets the two appended lines.

- [ ] **Step 7: Bring up Prometheus and run the infra-check plan**

```bash
docker compose up -d prometheus
docker compose run --rm jmeter -n -t /test-plans/_infra-check/empty.jmx
```

Expected: JMeter runs to completion with "Tidying up ... end of run" in the output.

- [ ] **Step 8: Verify the metrics port is reachable from a *different* container — not from inside jmeter itself**

This is the critical check for the `prometheus.ip=0.0.0.0` fix (Review Focus item 2) — curling from inside the `jmeter` container would succeed even with the bug, since `127.0.0.1` would still work there.

```bash
docker compose run --rm jmeter -n -t /test-plans/_infra-check/empty.jmx &
sleep 3
docker compose exec prometheus wget -qO- http://jmeter:9270/metrics | head -20
wait
```

Expected: Prometheus-format text output containing lines like `jmeter_threads ...` or JVM metrics (`jvm_memory_...`). If this returns nothing or `wget` can't connect, re-check that `prometheus.ip=0.0.0.0` actually landed in the built image's `jmeter.properties` (`docker run --rm boomi-jmeter:dev sh -c "grep prometheus $JMETER_HOME/bin/jmeter.properties"`).

- [ ] **Step 9: Confirm Prometheus itself is scraping it**

```bash
curl -s "http://localhost:9090/api/v1/query?query=jmeter_threads" | python3 -m json.tool
```

Expected: `"status": "success"` with at least one result in `data.result` (may require running Step 7 again right before this, since the `jmeter` container only lives for the duration of that one run).

- [ ] **Step 10: Commit**

```bash
git add jmeter prometheus docker-compose.yml test-plans/_infra-check
git commit -m "Add jmeter headless image and Prometheus scrape pipeline"
```

---

### Task 4: `jmeter-gui` noVNC authoring image

**Files:**
- Create: `jmeter-gui/Dockerfile`
- Create: `jmeter-gui/supervisord.conf`
- Create: `README.md`
- Modify: `docker-compose.yml` (add `jmeter-gui` service)

**Interfaces:**
- Consumes: `boomi-jmeter:dev` (Task 3) as its base image.
- Produces: a `boomi-jmeter-gui:dev` image serving a browser-accessible JMeter GUI at `http://localhost:6080/vnc.html`, sharing the same `test-plans/` bind mount as the headless `jmeter` service.

- [ ] **Step 1: Give the user exact content for `jmeter-gui/supervisord.conf`**

```ini
[supervisord]
nodaemon=true
logfile=/dev/stdout
logfile_maxbytes=0

[program:xvfb]
command=/usr/bin/Xvfb :1 -screen 0 1920x1080x24
autorestart=true
priority=10

[program:fluxbox]
command=/usr/bin/fluxbox -display :1
environment=DISPLAY=":1"
autorestart=true
priority=20

[program:x11vnc]
command=/usr/bin/x11vnc -display :1 -forever -shared -nopw -rfbport 5900
autorestart=true
priority=20

[program:novnc]
command=/usr/bin/websockify --web=/usr/share/novnc 6080 localhost:5900
autorestart=true
priority=30

[program:jmeter-gui]
command=/bin/sh -c "exec \"$JMETER_HOME/bin/jmeter\""
environment=DISPLAY=":1"
autorestart=true
priority=40
```

- [ ] **Step 2: Give the user exact content for `jmeter-gui/Dockerfile`**

```dockerfile
FROM boomi-jmeter:dev

ARG JMETER_STUDIO_VERSION=1.0.2

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        xvfb fluxbox x11vnc novnc websockify supervisor \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/QAInsights/jmeter-studio/releases/download/v${JMETER_STUDIO_VERSION}/jmeter-studio-oss-${JMETER_STUDIO_VERSION}.jar" \
    -o "${JMETER_HOME}/lib/ext/jmeter-studio-oss-${JMETER_STUDIO_VERSION}.jar"

COPY supervisord.conf /etc/supervisor/conf.d/jmeter-gui.conf

EXPOSE 6080
WORKDIR /test-plans
ENTRYPOINT ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
```

- [ ] **Step 3: Give the user the exact addition to `docker-compose.yml`**

Add this service alongside the existing `jmeter`/`prometheus` services (same file from Task 3):

```yaml
  jmeter-gui:
    build:
      context: ./jmeter-gui
    image: boomi-jmeter-gui:dev
    networks:
      - perf-test-net
    ports:
      - "6080:6080"
    volumes:
      - ./test-plans:/test-plans
    env_file:
      - .env
```

(`env_file` here matters if someone clicks JMeter's own "Start" button inside the noVNC session for a quick ad-hoc run instead of going through `run-test.sh` — without it, `${__env(...)}` credential lookups would resolve to empty strings in that one path.)

- [ ] **Step 4: Give the user exact content for `README.md`**

```markdown
# Boomi Performance Testing Stack

JMeter + Prometheus + Grafana load-testing stack for Boomi-published REST,
SOAP, and GraphQL APIs. See `docs/superpowers/specs/2026-10-12-jmeter-boomi-performance-stack-design.md`
for the full design.

## Authoring test plans

Two ways to build/edit `.jmx` files under `test-plans/`, same shared folder either way:

1. **Local JMeter GUI** — if you already have JMeter installed (e.g. via
   `brew install jmeter`), just open files directly from `test-plans/`.
   Optional: install [JMeter Studio](https://github.com/QAInsights/jmeter-studio)
   for the same modernized theme the `jmeter-gui` container ships with —
   download the latest jar from its Releases page, copy it into
   `$JMETER_HOME/lib/ext/`, restart JMeter, then Options → Themes.
2. **Browser-based GUI** — run `docker compose up -d jmeter-gui`, then open
   `http://localhost:6080/vnc.html`. No local JMeter install needed.

## Running a test

```bash
./run-test.sh rest/starter.jmx
```

Generates a `RUN_ID`, runs the test headlessly, and prints the id to filter
by in Grafana (`http://localhost:3000`, dashboard "Boomi Performance Testing
— JMeter Overview").

## Credentials

Copy the `BOOMI_TEST_TARGET_*` variables from `.env.example` into this
repo's `.env` and fill in real values for whatever API you're testing.
```

Ask the user to save this as `README.md` in the repo root.

- [ ] **Step 5: Build jmeter-gui (jmeter must already be built, since this Dockerfile's `FROM` depends on it locally)**

```bash
docker build -t boomi-jmeter:dev ./jmeter
docker build -t boomi-jmeter-gui:dev ./jmeter-gui
```

- [ ] **Step 6: Bring it up and confirm the browser path works**

```bash
docker compose up -d jmeter-gui
```

Open `http://localhost:6080/vnc.html` in a browser. Expected: a noVNC "Connect" button, then a desktop showing the JMeter GUI window with JMeter Studio's theme applied (check Options → Themes in the JMeter menu — a theme beyond the stock look should be selectable).

- [ ] **Step 7: Verify the shared volume round-trips both ways (Review Focus item 5)**

From the JMeter GUI inside the noVNC session: File → Save Test Plan As → save an empty test plan as `/test-plans/_infra-check/round-trip-check.jmx`.

Then from the host:

```bash
ls -la test-plans/_infra-check/round-trip-check.jmx
cat test-plans/_infra-check/round-trip-check.jmx | head -5
```

Expected: the file exists on the host with content (proves container→host direction). Then, from the host, touch the file again:

```bash
echo "<!-- host edit check -->" >> test-plans/_infra-check/round-trip-check.jmx
```

Back in the JMeter GUI inside noVNC: File → Open the same path again. Expected: JMeter either shows the file as modified-on-disk or reloads it without a permission error (proves host→container direction and rules out a UID mismatch silently blocking one direction). Delete this throwaway file afterward (`rm test-plans/_infra-check/round-trip-check.jmx`) — it was only for this check.

- [ ] **Step 8: Commit**

```bash
git add jmeter-gui docker-compose.yml README.md
git commit -m "Add jmeter-gui noVNC authoring image with JMeter Studio"
```

---

### Task 5: `grafana` image with pre-provisioned dashboard

**Files:**
- Create: `grafana/Dockerfile`
- Create: `grafana/provisioning/datasources/prometheus.yml`
- Create: `grafana/provisioning/dashboards/dashboards.yml`
- Create: `grafana/dashboards/jmeter-performance.json`
- Modify: `docker-compose.yml` (add `grafana` service)

**Interfaces:**
- Consumes: the Prometheus datasource at `http://prometheus:9090` (Task 3), with `uid: prometheus` set explicitly so the dashboard JSON can reference it deterministically.
- Produces: a `boomi-grafana:dev` image serving dashboards at `http://localhost:3000` (default login `admin`/`admin`), with a "Boomi Performance Testing — JMeter Overview" dashboard already present, parameterized by a `run_id` template variable. Depends on the metric names defined in Global Constraints (`jmeter_requests_total`, `jmeter_response_time_ms`, `jmeter_threads`) — Tasks 8–10 must use these exact names.

- [ ] **Step 1: Give the user exact content for `grafana/provisioning/datasources/prometheus.yml`**

```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    uid: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

- [ ] **Step 2: Give the user exact content for `grafana/provisioning/dashboards/dashboards.yml`**

```yaml
apiVersion: 1

providers:
  - name: "Boomi Performance Testing"
    orgId: 1
    folder: ""
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
```

- [ ] **Step 3: Give the user exact content for `grafana/dashboards/jmeter-performance.json`**

```json
{
  "title": "Boomi Performance Testing — JMeter Overview",
  "uid": "boomi-perf-jmeter",
  "schemaVersion": 39,
  "version": 1,
  "timezone": "browser",
  "time": { "from": "now-1h", "to": "now" },
  "refresh": "10s",
  "templating": {
    "list": [
      {
        "name": "run_id",
        "type": "query",
        "datasource": { "type": "prometheus", "uid": "prometheus" },
        "query": "label_values(jmeter_requests_total, run_id)",
        "refresh": 2,
        "multi": true,
        "includeAll": true,
        "current": { "text": "All", "value": "$__all" }
      }
    ]
  },
  "panels": [
    {
      "id": 1,
      "title": "Request Rate",
      "type": "timeseries",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "targets": [
        {
          "expr": "sum(rate(jmeter_requests_total{run_id=~\"$run_id\"}[1m])) by (run_id)",
          "legendFormat": "{{run_id}}"
        }
      ]
    },
    {
      "id": 2,
      "title": "p95 Response Time (ms)",
      "type": "timeseries",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "targets": [
        {
          "expr": "histogram_quantile(0.95, sum(rate(jmeter_response_time_ms_bucket{run_id=~\"$run_id\"}[1m])) by (le, run_id))",
          "legendFormat": "{{run_id}}"
        }
      ]
    },
    {
      "id": 3,
      "title": "Error Rate",
      "type": "timeseries",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "targets": [
        {
          "expr": "sum(rate(jmeter_requests_total{run_id=~\"$run_id\", code!~\"2..\"}[1m])) by (run_id)",
          "legendFormat": "{{run_id}}"
        }
      ]
    },
    {
      "id": 4,
      "title": "Active Threads",
      "type": "timeseries",
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 8 },
      "targets": [
        {
          "expr": "jmeter_threads{run_id=~\"$run_id\"}",
          "legendFormat": "{{run_id}}"
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Give the user exact content for `grafana/Dockerfile`**

```dockerfile
FROM grafana/grafana:latest

COPY provisioning/datasources /etc/grafana/provisioning/datasources
COPY provisioning/dashboards /etc/grafana/provisioning/dashboards
COPY dashboards /var/lib/grafana/dashboards
```

- [ ] **Step 5: Give the user the exact addition to `docker-compose.yml`**

```yaml
  grafana:
    build:
      context: ./grafana
    image: boomi-grafana:dev
    networks:
      - perf-test-net
    ports:
      - "3000:3000"
    volumes:
      - grafana-data:/var/lib/grafana
    depends_on:
      - prometheus
```

And add `grafana-data:` under the top-level `volumes:` key alongside `prometheus-data:`.

- [ ] **Step 6: Build and verify the dashboard loads structurally (no data yet — that's Task 11)**

```bash
docker build -t boomi-grafana:dev ./grafana
docker compose up -d prometheus grafana
sleep 3
curl -s -u admin:admin http://localhost:3000/api/dashboards/uid/boomi-perf-jmeter | python3 -m json.tool | head -20
```

Expected: JSON response containing the dashboard's title "Boomi Performance Testing — JMeter Overview" and its 4 panels — confirms provisioning worked and the JSON is schema-valid (Grafana would reject/error on a malformed dashboard JSON). Also open `http://localhost:3000` in a browser, log in with `admin`/`admin`, and confirm the dashboard opens without a "panel plugin not found" or "datasource not found" error (panels will show "No data" until Task 11 — that's expected here).

- [ ] **Step 7: Commit**

```bash
git add grafana docker-compose.yml
git commit -m "Add grafana image with pre-provisioned JMeter dashboard"
```

---

### Task 6: Wire `rest-target` and `graphql-target` into the stack

**Files:**
- Modify: `docker-compose.yml` (add `rest-target` and `graphql-target` services)

**Interfaces:**
- Consumes: `pythonicshahdev/fastapi-commercial-source:latest` (existing, unchanged) and `boomi-graphql-target:dev` (Task 2).
- Produces: both targets reachable on the `perf-test-net` network by service name (`rest-target:4005`, `graphql-target:8001`) for Tasks 8/9's test plans, and on published host ports (`8010`, `8011`) for manual smoke-testing.

- [ ] **Step 1: Give the user the exact addition to `docker-compose.yml`**

```yaml
  rest-target:
    image: pythonicshahdev/fastapi-commercial-source:latest
    networks:
      - perf-test-net
    ports:
      - "8010:4005"

  graphql-target:
    build:
      context: ./sample-services/graphql-strawberry
    image: boomi-graphql-target:dev
    networks:
      - perf-test-net
    ports:
      - "8011:8001"
```

- [ ] **Step 2: Bring both up and verify reachability**

```bash
docker compose up -d rest-target graphql-target
sleep 3
curl -sf http://localhost:8010/ping
curl -sf -X POST http://localhost:8011/graphql -H "Content-Type: application/json" \
  -d '{"query":"query { customers(limit: 1) { customerId } }"}'
```

Expected: `rest-target /ping` returns a 200 (check its actual body — it may differ from the sample service's `{"status":"ok"}`, since it's a different, pre-existing app). GraphQL query returns `CUST-000001` as before.

- [ ] **Step 3: Verify both are reachable *from the jmeter container* by service name (not just from the host)**

```bash
docker compose run --rm jmeter -n -t /test-plans/_infra-check/empty.jmx &
sleep 2
docker compose exec jmeter-gui sh -c "curl -sf http://rest-target:4005/ping && echo OK-REST"
docker compose exec jmeter-gui sh -c "curl -sf -X POST http://graphql-target:8001/graphql -H 'Content-Type: application/json' -d '{\"query\":\"query { customers(limit: 1) { customerId } }\"}' && echo OK-GRAPHQL"
wait
```

Expected: both `OK-REST` and `OK-GRAPHQL` print — confirms the Docker service-name DNS resolution that Tasks 8/9's test plans will rely on (`HTTPSampler.domain` = `rest-target` / `graphql-target`) actually works, not just host-published ports.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "Wire rest-target and graphql-target into the compose stack"
```

---

### Task 7: `run-test.sh` wrapper script

**Files:**
- Create: `run-test.sh`

**Interfaces:**
- Consumes: the `jmeter` service (Task 3).
- Produces: a `./run-test.sh <protocol>/<file>.jmx` entry point used by Tasks 8, 9, 10, and 11 — generates a `RUN_ID`, runs the given test plan via `-JRUN_ID=<value>`, writes a `.jtl` log to `test-plans/results/`.

- [ ] **Step 1: Give the user exact content for `run-test.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <protocol>/<test-plan>.jmx" >&2
  echo "Example: $0 rest/starter.jmx" >&2
  exit 1
fi

TEST_PLAN="$1"
RUN_ID="$(date -u +%Y%m%d-%H%M%S)"

echo "Running ${TEST_PLAN} with RUN_ID=${RUN_ID}"

docker compose run --rm jmeter \
  -n \
  -t "/test-plans/${TEST_PLAN}" \
  -l "/test-plans/results/${RUN_ID}.jtl" \
  -JRUN_ID="${RUN_ID}"

echo "Done. RUN_ID=${RUN_ID} — set Grafana's run_id variable to this value to see this run."
```

- [ ] **Step 2: Make it executable and smoke-test it against the infra-check plan**

```bash
chmod +x run-test.sh
./run-test.sh _infra-check/empty.jmx
ls test-plans/results/
```

Expected: script prints a generated `RUN_ID`, JMeter runs to completion, and a matching `.jtl` file appears under `test-plans/results/`.

- [ ] **Step 3: Commit**

```bash
git add run-test.sh
git commit -m "Add run-test.sh wrapper script"
```

---

### Task 8: REST starter template (list → detail correlation)

**Files:**
- Create: `test-plans/rest/starter.jmx`

**Interfaces:**
- Consumes: `rest-target:4005` (Task 6), `jmeter_requests_total`/`jmeter_response_time_ms` metric names (Global Constraints), `${BOOMI_TEST_TARGET_BASIC_AUTH_USER}`/`${BOOMI_TEST_TARGET_BASIC_AUTH_PASS}` from `.env.example` (Task 1), `run-test.sh` (Task 7).
- Produces: a working REST correlation example — `GET /customers?limit=5` → extract the first `customer_id` → `GET /customers/{id}` — that later real Boomi test plans can be cloned from.

Target endpoints confirmed live against `rest-target`'s real OpenAPI schema: `GET /customers?limit=5` returns a JSON array of objects with a `customer_id` field (e.g. `CUST-000001`); `GET /customers/{customer_id}` returns 200 for a valid id.

- [ ] **Step 1: Give the user exact content for `test-plans/rest/starter.jmx`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="REST Starter — Customer List + Detail" enabled="true">
      <stringProp name="TestPlan.comments">Boomi API Management-flavored starter: HTTP Authorization Manager demonstrates the ${__env(...)} credential pattern; JSON Extractor + Response Assertion demonstrate catching a silent correlation failure.</stringProp>
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
          <boolProp name="LoopController.continue_forever">false</boolProp>
          <intProp name="LoopController.loops">1</intProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">1</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">false</boolProp>
        <stringProp name="ThreadGroup.duration"></stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
      </ThreadGroup>
      <hashTree>
        <Arguments guiclass="ArgumentsPanel" testclass="Arguments" testname="run_id" enabled="true">
          <collectionProp name="Arguments.arguments">
            <elementProp name="run_id" elementType="Argument">
              <stringProp name="Argument.name">run_id</stringProp>
              <stringProp name="Argument.value">${__P(RUN_ID,manual-run)}</stringProp>
              <stringProp name="Argument.metadata">=</stringProp>
            </elementProp>
          </collectionProp>
        </Arguments>
        <hashTree/>
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
              <stringProp name="Authorization.username">${__env(BOOMI_TEST_TARGET_BASIC_AUTH_USER)}</stringProp>
              <stringProp name="Authorization.password">${__env(BOOMI_TEST_TARGET_BASIC_AUTH_PASS)}</stringProp>
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
          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Detail request must be 200 (catches silent extractor failure)" enabled="true">
            <collectionProp name="Asserion.test_strings">
              <stringProp name="49586">200</stringProp>
            </collectionProp>
            <stringProp name="Assertion.custom_message">Extractor likely returned NOT_FOUND instead of a real customer_id — check the JSON Extractor on &quot;GET Customers List&quot;.</stringProp>
            <stringProp name="Assertion.test_field">Assertion.response_code</stringProp>
            <boolProp name="Assertion.assume_success">false</boolProp>
            <intProp name="Assertion.test_type">8</intProp>
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
                <stringProp name="3">run_id</stringProp>
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
                <stringProp name="3">run_id</stringProp>
              </collectionProp>
              <stringProp name="collector.quantiles_or_buckets">50,100,250,500,1000,2500,5000,10000</stringProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">ResponseTime</stringProp>
            </elementProp>
          </collectionProp>
        </com.github.johrstrom.listener.PrometheusListener>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

Ask the user to save this exact content as `test-plans/rest/starter.jmx`.

- [ ] **Step 2: Validate it loads in JMeter without a parse error**

```bash
docker compose run --rm jmeter -n -t /test-plans/rest/starter.jmx -l /test-plans/results/validate-rest.jtl
```

Expected: JMeter runs both samplers successfully (`2 requests`, `0 errors` in the summary line). If JMeter reports an XML parse error, re-check the saved file against the content above for a copy/paste mistake (a stray unescaped character is the most likely cause).

- [ ] **Step 3: Deliberately break the correlation to confirm the assertion actually catches it (Review Focus item 1)**

Temporarily edit the saved file: change the JSON Extractor's `JSONPostProcessor.jsonPathExprs` value from `$[0].customer_id` to `$[0].nonexistent_field` (which will make it fall through to the `NOT_FOUND` default), then re-run:

```bash
docker compose run --rm jmeter -n -t /test-plans/rest/starter.jmx -l /test-plans/results/validate-rest-broken.jtl
```

Expected: the summary now shows `1 error` and the console prints the custom assertion failure message about the extractor. This proves the assertion is load-bearing, not decorative. **Revert the file back to `$[0].customer_id` afterward** and re-run Step 2 once more to confirm it's back to 0 errors before moving on.

- [ ] **Step 4: Run it for real via run-test.sh and confirm metrics land in Prometheus**

```bash
./run-test.sh rest/starter.jmx
curl -s "http://localhost:9090/api/v1/query?query=jmeter_requests_total" | python3 -m json.tool
```

Expected: a result with `label` values `"GET Customers List"` and `"GET Customer Detail"`, and a `run_id` label matching the `RUN_ID` the script printed.

- [ ] **Step 5: Commit**

```bash
git add test-plans/rest/starter.jmx
git commit -m "Add REST starter test plan with list-to-detail correlation"
```

---

### Task 9: GraphQL starter template (list → detail correlation)

**Files:**
- Create: `test-plans/graphql/starter.jmx`

**Interfaces:**
- Consumes: `graphql-target:8001` (Tasks 2, 6), `${BOOMI_TEST_TARGET_API_KEY}` from `.env.example` (Task 1), same metric names and `run_id` convention as Task 8.
- Produces: a working GraphQL correlation example, directly mirroring the pattern already proven in Task 2's `test_customer_query_by_id_matches_list_entry` pytest test, plus its own demonstration of the Boomi auth convention (API-key-style bearer header this time, vs. REST's Basic Auth, since the spec calls for "Basic Auth or API-key header" across the two starters). Demonstrates the difference from REST: a broken GraphQL correlation still returns HTTP 200 (GraphQL servers return 200 even for a "not found" result), so the assertion must check the **response body**, not the status code.

- [ ] **Step 1: Give the user exact content for `test-plans/graphql/starter.jmx`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="GraphQL Starter — Customer List + Detail" enabled="true">
      <stringProp name="TestPlan.comments">GraphQL returns HTTP 200 even when a query logically finds nothing, so correlation here is verified by asserting the detail response body contains the extracted id — a status-code check alone would not catch a broken extractor.</stringProp>
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
          <boolProp name="LoopController.continue_forever">false</boolProp>
          <intProp name="LoopController.loops">1</intProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">1</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">false</boolProp>
        <stringProp name="ThreadGroup.duration"></stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
      </ThreadGroup>
      <hashTree>
        <Arguments guiclass="ArgumentsPanel" testclass="Arguments" testname="run_id" enabled="true">
          <collectionProp name="Arguments.arguments">
            <elementProp name="run_id" elementType="Argument">
              <stringProp name="Argument.name">run_id</stringProp>
              <stringProp name="Argument.value">${__P(RUN_ID,manual-run)}</stringProp>
              <stringProp name="Argument.metadata">=</stringProp>
            </elementProp>
          </collectionProp>
        </Arguments>
        <hashTree/>
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
              <stringProp name="Header.value">Bearer ${__env(BOOMI_TEST_TARGET_API_KEY)}</stringProp>
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
          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Detail body must echo the extracted id (GraphQL returns 200 even on a miss)" enabled="true">
            <collectionProp name="Asserion.test_strings">
              <stringProp name="49587">"customerId":"${extracted_customer_id}"</stringProp>
            </collectionProp>
            <stringProp name="Assertion.custom_message">Response did not contain the extracted customerId — the JSON Extractor on &quot;GraphQL Customer List&quot; likely returned NOT_FOUND. A status-code check alone would have missed this, since GraphQL returns 200 regardless.</stringProp>
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
                <stringProp name="3">run_id</stringProp>
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
                <stringProp name="3">run_id</stringProp>
              </collectionProp>
              <stringProp name="collector.quantiles_or_buckets">50,100,250,500,1000,2500,5000,10000</stringProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">ResponseTime</stringProp>
            </elementProp>
          </collectionProp>
        </com.github.johrstrom.listener.PrometheusListener>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

Ask the user to save this exact content as `test-plans/graphql/starter.jmx`.

- [ ] **Step 2: Validate it loads and runs clean**

```bash
docker compose run --rm jmeter -n -t /test-plans/graphql/starter.jmx -l /test-plans/results/validate-graphql.jtl
```

Expected: `2 requests`, `0 errors`.

- [ ] **Step 3: Deliberately break the correlation to confirm the body assertion catches what a status-code check would miss**

Temporarily change the JSON Extractor's path from `$.data.customers[0].customerId` to `$.data.customers[0].nonexistentField`, re-run:

```bash
docker compose run --rm jmeter -n -t /test-plans/graphql/starter.jmx -l /test-plans/results/validate-graphql-broken.jtl
```

Expected: `1 error` — note that both requests still return HTTP 200 (check the `.jtl` file's response codes to confirm this explicitly), and it's the **body-content assertion** that catches the failure, not a status code. **Revert the extractor path back** and confirm 0 errors again before moving on.

- [ ] **Step 4: Run it for real and confirm metrics**

```bash
./run-test.sh graphql/starter.jmx
curl -s "http://localhost:9090/api/v1/query?query=jmeter_requests_total" | python3 -m json.tool
```

Expected: `label` values `"GraphQL Customer List"` and `"GraphQL Customer Detail"` now also present, alongside the REST ones from Task 8, each tagged with their own `run_id`.

- [ ] **Step 5: Commit**

```bash
git add test-plans/graphql/starter.jmx
git commit -m "Add GraphQL starter test plan with list-to-detail correlation"
```

---

### Task 10: SOAP correlation smoke-test plan

**Files:**
- Create: `test-plans/soap/smoke-test.jmx`

**Interfaces:**
- Consumes: the public `www.dneonline.com/calculator.asmx` service (no bundled target), same metric names and `run_id` convention as Tasks 8/9.
- Produces: proof the harness's SOAP *capability* works end to end, without owning any SOAP server. `Add(5, 7)` → extract `AddResult` (12) → `Add(12, 3)` → expect `AddResult` = 15. Live-verified against the real service while writing this plan.

- [ ] **Step 1: Give the user exact content for `test-plans/soap/smoke-test.jmx`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="SOAP Correlation Smoke Test — Calculator Add" enabled="true">
      <stringProp name="TestPlan.comments">No bundled SOAP target by design (see spec Non-goals) — hits the public dneonline.com calculator demo. Proves SOAP capability only; not a Boomi-auth starter template.</stringProp>
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
          <boolProp name="LoopController.continue_forever">false</boolProp>
          <intProp name="LoopController.loops">1</intProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">1</stringProp>
        <stringProp name="ThreadGroup.ramp_time">1</stringProp>
        <boolProp name="ThreadGroup.scheduler">false</boolProp>
        <stringProp name="ThreadGroup.duration"></stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
      </ThreadGroup>
      <hashTree>
        <Arguments guiclass="ArgumentsPanel" testclass="Arguments" testname="run_id" enabled="true">
          <collectionProp name="Arguments.arguments">
            <elementProp name="run_id" elementType="Argument">
              <stringProp name="Argument.name">run_id</stringProp>
              <stringProp name="Argument.value">${__P(RUN_ID,manual-run)}</stringProp>
              <stringProp name="Argument.metadata">=</stringProp>
            </elementProp>
          </collectionProp>
        </Arguments>
        <hashTree/>
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
          <ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Final sum must be exactly 15 (5+7+3)" enabled="true">
            <collectionProp name="Asserion.test_strings">
              <stringProp name="49588">&lt;AddResult&gt;15&lt;/AddResult&gt;</stringProp>
            </collectionProp>
            <stringProp name="Assertion.custom_message">Expected &lt;AddResult&gt;15&lt;/AddResult&gt; — if this fails, the Regex Extractor on &quot;SOAP Add 5+7&quot; likely returned NOT_FOUND instead of 12.</stringProp>
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
                <stringProp name="3">run_id</stringProp>
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
                <stringProp name="3">run_id</stringProp>
              </collectionProp>
              <stringProp name="collector.quantiles_or_buckets">50,100,250,500,1000,2500,5000,10000</stringProp>
              <stringProp name="listener.collector.listen_to">samples</stringProp>
              <stringProp name="listener.collector.measuring">ResponseTime</stringProp>
            </elementProp>
          </collectionProp>
        </com.github.johrstrom.listener.PrometheusListener>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

Ask the user to save this exact content as `test-plans/soap/smoke-test.jmx`.

- [ ] **Step 2: Validate it against the live public service**

```bash
docker compose run --rm jmeter -n -t /test-plans/soap/smoke-test.jmx -l /test-plans/results/validate-soap.jtl
```

Expected: `2 requests`, `0 errors`. (This depends on `www.dneonline.com` being up — it was confirmed live and returning `12` for `Add(5,7)` while writing this plan; if it's down, this task's validation is blocked on an external dependency outside this project's control, not a bug in the test plan.)

- [ ] **Step 3: Deliberately break the correlation to confirm the assertion catches a wrong sum**

Temporarily change `<intB>3</intB>` in the second sampler's body to `<intB>99</intB>`, re-run:

```bash
docker compose run --rm jmeter -n -t /test-plans/soap/smoke-test.jmx -l /test-plans/results/validate-soap-broken.jtl
```

Expected: `1 error` — the response now contains `<AddResult>111</AddResult>` instead of `15`, and the assertion fails with the custom message. **Revert back to `<intB>3</intB>`** and confirm 0 errors again.

- [ ] **Step 4: Run it for real and confirm metrics**

```bash
./run-test.sh soap/smoke-test.jmx
curl -s "http://localhost:9090/api/v1/query?query=jmeter_requests_total" | python3 -m json.tool
```

Expected: `label` values `"SOAP Add 5+7"` and `"SOAP Add Extracted+3"` now present alongside the REST and GraphQL ones.

- [ ] **Step 5: Commit**

```bash
git add test-plans/soap/smoke-test.jmx
git commit -m "Add SOAP correlation smoke-test plan against public calculator demo"
```

---

### Task 11: Smoke-test automation + full end-to-end validation

**Files:**
- Create: `smoke-test.sh`

**Interfaces:**
- Consumes: every prior task's deliverable — this is the integration point that proves the whole stack works together, matching the spec's Testing & Validation checklist line-for-line.

- [ ] **Step 1: Give the user exact content for `smoke-test.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

echo "=== 1. Bring up long-running services ==="
docker compose up -d prometheus grafana rest-target graphql-target jmeter-gui
sleep 5
pass "docker compose up -d completed"

echo "=== 2. Target reachability ==="
curl -sf http://localhost:8010/ping > /dev/null && pass "rest-target /ping reachable" || fail "rest-target /ping unreachable"
curl -sf -X POST http://localhost:8011/graphql -H "Content-Type: application/json" \
  -d '{"query":"query { customers(limit: 1) { customerId } }"}' > /dev/null \
  && pass "graphql-target reachable" || fail "graphql-target unreachable"
curl -sf "http://www.dneonline.com/calculator.asmx?WSDL" > /dev/null \
  && pass "public SOAP demo reachable" || fail "public SOAP demo unreachable (external dependency, not this stack)"

echo "=== 3. Grafana + Prometheus reachability ==="
curl -sf http://localhost:9090/-/healthy > /dev/null && pass "prometheus healthy" || fail "prometheus not healthy"
curl -sf http://localhost:3000/api/health > /dev/null && pass "grafana healthy" || fail "grafana not healthy"

echo "=== 4. jmeter-gui noVNC reachability ==="
curl -sf http://localhost:6080/vnc.html > /dev/null && pass "jmeter-gui noVNC page reachable" || fail "jmeter-gui noVNC unreachable"

echo "=== Automated checks complete. Remaining checks require a real test run and manual inspection: ==="
echo "  1. ./run-test.sh rest/starter.jmx"
echo "  2. ./run-test.sh rest/starter.jmx       (run twice — note both printed RUN_IDs)"
echo "  3. Open http://localhost:3000, dashboard 'Boomi Performance Testing — JMeter Overview'"
echo "     - confirm the run_id dropdown lists BOTH run ids from step 2, not just one blended series"
echo "     - confirm Request Rate / p95 Response Time / Active Threads panels show non-empty data"
echo "  4. Edit a .jmx from the local JMeter GUI, confirm the change is visible from jmeter-gui's noVNC session (and vice versa)"
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x smoke-test.sh
./smoke-test.sh
```

Expected: all automated `PASS` lines print; script exits 0.

- [ ] **Step 3: Run the manual portion — confirm two distinct runs are distinguishable (Review Focus item 4)**

```bash
./run-test.sh rest/starter.jmx
sleep 2
./run-test.sh rest/starter.jmx
```

Note both printed `RUN_ID` values, then:

```bash
curl -s "http://localhost:9090/api/v1/query?query=label_values(jmeter_requests_total,run_id)" | python3 -m json.tool
```

Expected: the response includes both `RUN_ID` values as separate entries — confirms `run_id` is genuinely distinguishing runs, not just present-but-unused.

- [ ] **Step 4: Confirm the Grafana dashboard actually renders this data**

Open `http://localhost:3000`, navigate to "Boomi Performance Testing — JMeter Overview". Set the `run_id` variable dropdown to "All" or select the two specific run ids from Step 3. Expected: Request Rate, p95 Response Time, and Active Threads panels show non-empty lines, one per `run_id`.

- [ ] **Step 5: Final dual-authoring-path confirmation**

Run through Task 4 Step 6 one more time now that real starter templates exist: open `test-plans/rest/starter.jmx` from the local host JMeter GUI, make a trivial comment edit, save, then confirm from `jmeter-gui`'s noVNC session (File → Open the same path) that the change is visible. This is the last Review Focus item (bind-mount round-trip) — it was checked once already in Task 4 with a throwaway file; this repeats it against a real, permanent deliverable to be sure nothing about volume permissions changed as the stack grew.

- [ ] **Step 6: Commit**

```bash
git add smoke-test.sh
git commit -m "Add smoke-test.sh and complete end-to-end stack validation"
```

---

## What this plan does not do

Per the spec's Non-goals and Open Items: it does not push any image to Docker Hub (local `:dev` tags only — ask the user before publishing), does not build a Helm chart, does not implement distributed/parallel load generation, does not wire up trace/span correlation with the OTel observability stack, and does not build a Mashery/CAM starter template or MCP Gateway support. All of these remain explicitly deferred, as decided during brainstorming.
