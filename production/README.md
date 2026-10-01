# TraceNest Production Deployment Configurations

This directory contains standalone, production-ready configurations split cleanly by host role:

```text
production/
├── README.md
├── app-server/                    # Deploy onto your Django Application Host
│   ├── .env.example
│   ├── docker-compose.yml         # Runs OTel Collector as a sidecar
│   └── otel-collector-config.yaml # OTel pipeline, spanmetrics & Tempo exporter
└── observability-server/          # Deploy onto your dedicated Observability Host
    ├── .env.example
    ├── docker-compose.yml         # Runs Tempo, Prometheus & Grafana
    ├── tempo/
    │   └── tempo-config.yaml
    ├── prometheus/
    │   ├── prometheus.yml
    │   └── targets/
    │       └── otel-collector.json
    └── grafana/
        ├── dashboards/            # Pre-configured TraceNest APM dashboards
        └── provisioning/          # Automated datasource & dashboard mounting
```

---

## 1. Observability Server Setup (Deploy 1st)

Copy `production/observability-server/` to your observability host.

```bash
cd observability-server/
cp .env.example .env

# Edit .env and set a secure Grafana admin password:
# GF_SECURITY_ADMIN_PASSWORD=your-secure-password
nano .env

# Update Prometheus target to point to your App Server's IP/DNS:
nano prometheus/targets/otel-collector.json
# Change <APP_SERVER_IP_OR_HOSTNAME> to your app host's private IP (e.g. 10.0.1.20:8889)

# Start services
docker compose up -d
```

### Verification
```bash
# Check Tempo readiness
curl -fsS http://localhost:3200/ready

# Check Prometheus target status
curl -s http://localhost:9090/api/v1/targets
```

---

## 2. App Server Setup (Deploy 2nd)

Copy `production/app-server/` to your application host.

```bash
cd app-server/
cp .env.example .env

# Edit .env and set the Observability Server's private IP/DNS:
# TEMPO_OTLP_ENDPOINT=10.0.1.50:4317
nano .env

# Start the OTel Collector sidecar
docker compose up -d
```

### Django Application Configuration

1. Install TraceNest SDK in your Django project:
   ```bash
   pip install tracenest
   ```

2. Initialize TraceNest in `settings.py`:
   ```python
   import tracenest

   tracenest.init(
       project_name="my-django-app",
       environment="production",
       endpoint="http://127.0.0.1:4318",
   )
   ```

---

## 3. Firewall & Security Matrix

| Host | Port | Direction | Source | Purpose |
|---|---|---|---|---|
| **Observability Host** | `4317/tcp` | Inbound | App Server IP only | OTel Collector pushes traces to Tempo |
| **Observability Host** | `3200/tcp` | Inbound | Internal network / VPN | Tempo query API |
| **Observability Host** | `9090/tcp` | Inbound | Internal network / VPN | Prometheus Web & API |
| **Observability Host** | `3000/tcp` | Inbound | Internal network / VPN | Grafana UI |
| **App Host** | `8889/tcp` | Inbound | Observability Host IP only | Prometheus scrapes spanmetrics |

---

## 4. Verification & Diagnostics

1. **Verify App trace export**:
   Make a request to your Django service and check the response headers for `x-trace-id`:
   ```bash
   curl -i http://localhost:8000/api/your-endpoint/
   ```

2. **Verify Collector health**:
   ```bash
   docker compose logs otel-collector | grep -iE "error|exporting failed"
   ```

3. **Open Grafana**:
   Visit `http://<OBSERVABILITY_SERVER_IP>:3000` to view the **TraceNest APM** dashboards.
