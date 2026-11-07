# boomi-loadtest-graphql-target

A small GraphQL API to practice load testing against. Part of the **Boomi Load Testing Toolkit**. It returns fixed, fake customer and product data, so it is safe to hammer and needs no credentials.

## What it serves

- `POST /graphql` (Strawberry on FastAPI) with queries `customers(limit)`, `customer(customerId)`, `products(limit)` and `product(materialNumber)`
- `GET /ping` returns `{"status": "ok"}`, handy for health checks
- Listens on port **8001**

## Quick start

```bash
docker run --rm -p 8011:8001 pythonicshahdev/boomi-loadtest-graphql-target:latest
curl -s localhost:8011/graphql -H 'content-type: application/json' \
  -d '{"query":"{ customers(limit: 2) { customerId } }"}'
```

Or run the whole load-testing stack, which includes it:

```bash
git clone https://github.com/pythonicsshahdev/BOOMI_Performance_Testing_POC
cd BOOMI_Performance_Testing_POC && ./setup.sh
```

## Notes

This is a demo target, not a production service. Don't expose it publicly.

## Tags

`latest` and a dated tag (`YYYY-MM-DD`). Multi-arch: `linux/amd64`, `linux/arm64`.

## Source and license

https://github.com/pythonicsshahdev/BOOMI_Performance_Testing_POC (MIT)
