# Grafana Tempo Server Setup Guide (Docker)

A streamlined guide for deploying and running **Grafana Tempo** using Docker Compose.

---

## 1. Ports to Open

Ensure the following ports are accessible on the server firewall:

| Port | Protocol | Purpose |
|------|----------|---------|
| `4317` | TCP (gRPC) | OTLP trace ingestion from OpenTelemetry Collector |
| `4318` | TCP (HTTP) | OTLP HTTP trace ingestion (optional) |
| `3200` | TCP (HTTP) | Tempo HTTP API, query endpoint, and health check |

---

## 2. Directory Setup

Create the working and data directories on the server:

```bash
sudo mkdir -p /opt/tempo/data/traces /opt/tempo/data/wal
sudo chown -R 10001:10001 /opt/tempo/data
cd /opt/tempo
```

> **Note**: Tempo's official container runs as user UID `10001`. Setting ownership ensures it has read/write permissions for trace blocks and WAL.

---

## 3. Configuration Files

### 3.1 `tempo-config.yaml`

Create `/opt/tempo/tempo-config.yaml`:

```yaml
stream_over_http_enabled: true

server:
  http_listen_port: 3200
  log_level: info

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318

ingester:
  max_block_duration: 5m
  trace_idle_period: 10s

compactor:
  compaction:
    block_retention: 336h       # 14 days retention (adjust as needed)
    compacted_block_retention: 1h
    compaction_cycle: 30m

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/traces
    wal:
      path: /var/tempo/wal
```
---

### 3.2 `docker-compose.yml`

Create `/opt/tempo/docker-compose.yml`:

```yaml
services:
  tempo:
    image: grafana/tempo:2.4.1
    container_name: tempo
    restart: unless-stopped
    command: ["-config.file=/etc/tempo/tempo.yaml"]
    volumes:
      - ./tempo-config.yaml:/etc/tempo/tempo.yaml:ro
      - /opt/tempo/data:/var/tempo
    ports:
      - "4317:4317" # OTLP gRPC
      - "4318:4318" # OTLP HTTP
      - "3200:3200" # Tempo Query & API
```

---

## 4. Start Tempo

Start the container in detached mode:

```bash
docker compose up -d
```

View the container logs:

```bash
docker compose logs -f tempo
```

---

## 5. Health Check & Verification

Run these commands to confirm Tempo is healthy and listening:

```bash
# 1. Readiness probe (returns HTTP 200 "ready")
curl -i http://localhost:3200/ready

# 2. Check build info
curl http://localhost:3200/status/buildinfo
```

---

## 6. Useful Commands

```bash
# View live logs
docker compose logs -f tempo

# Restart Tempo
docker compose restart tempo

# Stop Tempo
docker compose down

# Check storage size
du -sh /opt/tempo/data/*
```
