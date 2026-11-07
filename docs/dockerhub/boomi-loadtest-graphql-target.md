# 🧪 boomi-loadtest-graphql-target

**A small GraphQL API to practise load testing on.** It serves fixed, fake customer and product data, so you can hammer it without hurting anything and without any credentials. Part of the [Boomi Load Testing Toolkit](https://github.com/pythonicsshahdev/boomi-load-testing-toolkit).

> 🛑 This is a demo target, not a production service. Don't expose it to the internet.

## ✨ What it serves

| Endpoint | What it does |
|---|---|
| 📮 `POST /graphql` | GraphQL (Strawberry on FastAPI) |
| 💓 `GET /ping` | returns `{"status": "ok"}`, handy for health checks |

**Queries you can try:**

| Query | Returns |
|---|---|
| `customers(limit)` | a list of customers |
| `customer(customerId)` | one customer |
| `products(limit)` | a list of products |
| `product(materialNumber)` | one product |

It listens on port **8001**.

## 🏁 Try it in 10 seconds

```bash
docker run --rm -p 8011:8001 pythonicshahdev/boomi-loadtest-graphql-target:latest
```

In another terminal:

```bash
curl -s localhost:8011/graphql -H 'content-type: application/json' \
  -d '{"query":"{ customers(limit: 2) { customerId } }"}'
```

You should get back `{"data":{"customers":[{"customerId":"CUST-000001"},{"customerId":"CUST-000002"}]}}`. ✅

## 🚀 Load test it

The toolkit includes ready-made GraphQL test plans and a dashboard:

```bash
git clone https://github.com/pythonicsshahdev/boomi-load-testing-toolkit
cd boomi-load-testing-toolkit
./setup.sh
```

## 🏷️ Tags and platforms

| Tag | Meaning |
|---|---|
| `latest` | the newest release |
| `YYYY-MM-DD` | a pinned release |

🖥️ Multi-arch: `linux/amd64` and `linux/arm64`.

## 🔗 Related images

- 🚀 [`boomi-loadtest-jmeter`](https://hub.docker.com/r/pythonicshahdev/boomi-loadtest-jmeter): the load generator
- 📊 [`boomi-loadtest-grafana`](https://hub.docker.com/r/pythonicshahdev/boomi-loadtest-grafana): the dashboard

## 📄 Source and license

https://github.com/pythonicsshahdev/boomi-load-testing-toolkit, released under the **MIT** license.
