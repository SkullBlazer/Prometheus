# Observability Benchmark Run

Generated at: 2026-03-28T14:40:32+00:00
Detected mode: prometheus-only

## Endpoint Detection

- Prometheus up: True
- Thanos query up: False

## Instant Query Latency (avg ms)

| Endpoint | Query | Avg ms | P95 ms | Success % |
|---|---|---:|---:|---:|
| prometheus | up | 0.60 | 0.70 | 100.0 |
| prometheus | ingest_rate | 0.68 | 0.73 | 100.0 |
| prometheus | http_req_rate | 0.74 | 0.83 | 100.0 |
| prometheus | scrape_duration_avg | 0.56 | 0.66 | 100.0 |

## Load Test

| Endpoint | Req/s | Avg ms | P95 ms | Success % |
|---|---:|---:|---:|---:|
| prometheus | 4852.70 | 1.65 | 2.47 | 100.0 |

## Container Resource Snapshot

| Container | CPU % | Mem MiB | Mem % |
|---|---:|---:|---:|
| prometheus | 0.58 | 48.34 | 0.15 |
