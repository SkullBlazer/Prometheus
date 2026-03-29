# Observability Benchmark Run

Generated at: 2026-03-28T14:37:33+00:00
Detected mode: prometheus+thanos

## Endpoint Detection

- Prometheus up: True
- Thanos query up: True

## Instant Query Latency (avg ms)

| Endpoint | Query | Avg ms | P95 ms | Success % |
|---|---|---:|---:|---:|
| prometheus | up | 0.38 | 0.55 | 100.0 |
| prometheus | ingest_rate | 0.35 | 0.37 | 100.0 |
| prometheus | http_req_rate | 0.48 | 0.59 | 100.0 |
| prometheus | scrape_duration_avg | 0.47 | 0.61 | 100.0 |
| thanos_query | up | 0.81 | 1.05 | 100.0 |
| thanos_query | ingest_rate | 0.73 | 0.87 | 100.0 |
| thanos_query | http_req_rate | 1.44 | 1.83 | 100.0 |
| thanos_query | scrape_duration_avg | 0.66 | 0.70 | 100.0 |

## Load Test

| Endpoint | Req/s | Avg ms | P95 ms | Success % |
|---|---:|---:|---:|---:|
| prometheus | 4470.20 | 1.79 | 2.64 | 100.0 |
| thanos_query | 2613.95 | 3.06 | 4.45 | 100.0 |

## Container Resource Snapshot

| Container | CPU % | Mem MiB | Mem % |
|---|---:|---:|---:|
| thanos-query | 0.00 | 31.52 | 0.10 |
| thanos-sidecar | 0.00 | 114.60 | 0.37 |
| minio | 0.02 | 86.38 | 0.28 |
| prometheus | 0.00 | 45.65 | 0.15 |
