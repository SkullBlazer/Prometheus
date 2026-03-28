# Prometheus-Only Setup

This branch demonstrates a **standalone Prometheus** monitoring stack with local storage only.

## What's Running

- **Prometheus** (port `9090`): Metrics collection and storage
  - Scrapes its own metrics every 5 seconds
  - Stores data locally in `/prometheus` volume
  - TSDB blocks rotate every 2 minutes (for demo purposes)

## How to Run

1. **Start the stack:**
   ```bash
   docker compose up -d
   ```

2. **Access Prometheus UI:**
   Open `http://localhost:9090` in your browser

3. **Query metrics:**
   - Try typing `up` in the query box to see scrape status
   - Try `prometheus_tsdb_symbol_table_size_bytes` to see storage metrics

## How to Stop

```bash
docker compose down
```

## Data Retention

- Metrics are stored **only locally** in the `prometheus_data` volume
- If the volume is deleted, all historical data is lost
- No long-term retention available

## Use Case

This setup is ideal for:
- Single-instance monitoring
- Development/testing
- Short-term metrics inspection
- Low operational complexity

## Comparison

To compare with **Prometheus + Thanos**, switch to the `main` branch:
```bash
git checkout main
```

See [../main/README.md](../main/README.md) for details on the full observability stack.
