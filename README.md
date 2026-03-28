# Prometheus + Thanos Observability Stack

This branch demonstrates a **complete observability stack** with Prometheus for scraping, Thanos for long-term storage and global querying, and MinIO for object storage.

## What's Running

- **Prometheus** (port `9090`): Metrics collection and local TSDB
  - Scrapes its own metrics every 5 seconds
  - Stores data locally in `/prometheus` volume
  - TSDB blocks rotate every 2 minutes (for lab/demo purposes)

- **MinIO** (ports `9000` S3 API, `9001` console): Object storage backend
  - S3-compatible storage for Thanos blocks
  - Console available at `http://localhost:9001` (user: `minio`, password: `minio123`)
  - Default bucket: `thanos`

- **Thanos Sidecar** (port `10902`): Ships Prometheus blocks to MinIO
  - Watches `/prometheus` volume for completed TSDB blocks
  - Uploads blocks to MinIO `thanos` bucket
  - Exposes gRPC endpoint for Thanos Query

- **Thanos Query** (port `10903`): Global query layer
  - Federates queries across Prometheus and Thanos Sidecar
  - Merges results from multiple sources
  - Deduplicates metrics by external labels

## How to Run

1. **Start the stack:**
   ```bash
   docker compose up -d
   ```

2. **Wait for initial block upload:**
   - First blocks are created ~2 minutes after startup
   - Sidecar logs: `docker compose logs thanos-sidecar --tail 50`
   - Look for: `msg="upload new block"`

3. **Access Services:**
   - Prometheus: `http://localhost:9090`
   - Thanos Query: `http://localhost:10903`
   - MinIO Console: `http://localhost:9001` (minio / minio123)

4. **Query Metrics:**
   - In Thanos Query or Prometheus, try:
     - `up` to see scrape status
     - `prometheus_tsdb_symbol_table_size_bytes` to see storage metrics
     - `prometheus_http_requests_total` to see request patterns

5. **Verify Data in MinIO:**
   - Open MinIO console `http://localhost:9001`
   - Navigate to `Buckets > thanos`
   - You'll see block directories like `thanos/upload/01KMK73DM2BKRZXT6NZ6BMJPDS/`

## How to Stop

```bash
docker compose down
```

## Architecture Flow

```
Prometheus (scrape)
       ↓
Local /prometheus volume (TSDB blocks)
       ↓
Thanos Sidecar (watches, ships blocks)
       ↓
MinIO (object storage, durable)
       ↓
Thanos Query (reads from sidecar + MinIO)
       ↓
Query API (http://localhost:10903)
```

## Key Features Demonstrated

1. **Long-term Retention**: Data persisted in MinIO indefinitely (vs. Prometheus-only local storage)
2. **Block Shipping**: Sidecar automatically uploads completed blocks to object store
3. **Deduplication**: External labels (`cluster: thanos-lab`, `replica: 0`) enable HA dedup
4. **Federation**: Thanos Query merges data from multiple sidecars (useful for multi-cluster setups)

## Configuration Files

- `prometheus.yml`: Prometheus scrape config + external labels for Thanos dedup
- `objstore.yml`: S3/MinIO connection details for Thanos

## Use Case

This setup is ideal for:
- Multi-cluster monitoring
- Long-term metrics retention (compliance, analytics)
- HA monitoring with deduplication
- Centralized global query across regions/environments
- Data archival and historical analysis

## Troubleshooting

**No data appearing in MinIO?**
- Check sidecar logs: `docker compose logs thanos-sidecar`
- Wait at least 2-3 minutes for first block to be created and uploaded
- Ensure external labels are configured in `prometheus.yml`

**Cannot authenticate to MinIO?**
- MinIO credentials: `minio` / `minio123` (configured in `docker-compose.yml`)

## Comparison

To compare with **Prometheus-only**, switch to the `prometheus-only` branch:
```bash
git checkout prometheus-only
```

See the `prometheus-only/README.md` for details on the simplified setup.
