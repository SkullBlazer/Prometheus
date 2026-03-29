# Benchmark Comparison

Left run: prom_only.json
Right run: prom_thanos.json

## Instant Query Latency

| Query | Left Prom avg ms | Right Prom avg ms | Right Thanos avg ms | Thanos overhead vs right Prom % |
|---|---:|---:|---:|---:|
| up | 0.60 | 0.38 | 0.81 | 113.12 |
| ingest_rate | 0.68 | 0.35 | 0.73 | 109.71 |
| http_req_rate | 0.74 | 0.48 | 1.44 | 202.87 |
| scrape_duration_avg | 0.56 | 0.47 | 0.66 | 40.97 |

## Load Throughput

| Metric | Value |
|---|---:|
| left_prometheus_qps | 4852.70 |
| right_prometheus_qps | 4470.20 |
| right_thanos_query_qps | 2613.95 |

## Memory Snapshot

| Metric | MiB |
|---|---:|
| left_total_mem_mib | 48.34 |
| right_total_mem_mib | 278.15 |
| delta_mem_mib | 229.81 |

## Interpretation

### Prometheus-only Pros
- Lower memory footprint by about 229.81 MiB in this run.
- Simpler deployment and fewer moving parts to operate.

### Prometheus-only Cons
- No dedicated global query layer for multi-cluster aggregation.
- Local-only retention unless extra remote storage components are added.

### Thanos Stack Pros
- Provides an additional query layer that can aggregate across multiple Prometheus instances.
- Can use object storage for long-term retention (if sidecar/store components are enabled).

### Thanos Stack Cons
- Higher memory footprint by about 229.81 MiB in this run.
- Higher operational complexity due to extra components and object storage integration.
