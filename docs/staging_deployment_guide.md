# TraceNest Staging Deployment Guide
### testpress_python → OTEL Collector → Tempo → Prometheus → Grafana

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              APP SERVER                      │
│                                             │
│  Django (bare metal / Gunicorn)             │
│  + tracenest SDK (pip installed)            │
│       │ OTLP HTTP :4318 (localhost)         │
│       ▼                                     │
│  OTEL Collector  (Docker sidecar)           │
│       │ :4317 → Tempo Server                │
│       │ :8889 ← Prometheus Server scrapes   │
└─────────────────────────────────────────────┘

┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ TEMPO SERVER │  │PROMETHEUS SERVER │  │  GRAFANA SERVER  │
│              │  │                  │  │                  │
│ Tempo :4317  │  │ Prometheus :9090 │  │ Grafana :3000    │
│ Tempo :3200  │  │ scrapes App :8889│  │ → Prometheus     │
└──────────────┘  └──────────────────┘  │ → Tempo          │
                                         └──────────────────┘
```

---

## 1. App Server Changes

### 1a. Install TraceNest SDK

```bash
pip install tracenest

# Add to your requirements file
echo "tracenest" >> requirements/production.txt
```

---

### 1b. Add `tracenest.init()` to `staging.py`

**File:** `testpress/testpress/settings/staging.py`

```diff
  from .production import *

  STATIC_URL = "https://static.testpress.in/static-staging/"

  IS_STAGING_SERVER = True
  CELERY_TASK_ROUTES = {}
  CELERY_TASK_DEFAULT_QUEUE = "testpress_staging"


  sentry_sdk.init(
      dsn="https://8f008334c7da410f6de6c71b1db922cb@sentry.testpress.in/27",
      integrations=[DjangoIntegration(), CeleryIntegration(), RedisIntegration(), LoggingIntegration(level="ERROR")],
      send_default_pii=True,
      traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
  )

+ # ── TraceNest OpenTelemetry ──────────────────────────────────────────────
+ try:
+     import tracenest
+     tracenest.init(
+         project_name="testpress",
+         cluster_name="staging",
+         sample_rate=1.0,
+     )
+ except ImportError:
+     pass  # SDK not installed — app continues normally
+ except Exception:
+     pass  # Init failed — app continues normally
+ # ────────────────────────────────────────────────────────────────────────
```

> **Why `try/except`?** Sentry can do it bare because it's confirmed installed.
> Until tracenest is in your requirements and deployed, this guard ensures
> Django won't fail to start if the package is missing.

---

### 1c. Add env var to `.envs/.env` on staging server

```bash
# .envs/.env
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

The SDK reads this automatically — no code change needed.

---

### 1d. Gunicorn config

**File:** `scripts/gunicorn_conf_staging.py` *(or existing `scripts/gunicorn_conf.py`)*

```python
from multiprocessing import cpu_count

bind = "127.0.0.1:8000"
workers = cpu_count() * 2 + 1
logfile = "/var/log/gunicorn/gunicorn.log"
graceful_timeout = 30
timeout = 30
preload_app = True
```

> **Note on `preload_app = True`**: Modern OpenTelemetry SDK and TraceNest handle Gunicorn worker forks natively without requiring any manual `post_fork` hooks. You can safely keep `preload_app = True` exactly as used in production.

Start gunicorn with:
```bash
gunicorn -c scripts/gunicorn_conf_staging.py testpress.staging_wsgi:application
```


---

### 1e. OTEL Collector Docker sidecar on App Server

**File:** `docker-compose.otel.yml` *(new file, App Server only)*

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.96.0
    container_name: tp-otel-collector
    command: ["--config=/etc/otelcol-contrib/config.yaml"]
    extra_hosts:
      - "host.docker.internal:host-gateway"   # Linux: reach host's postgres
    volumes:
      - ./docker/otel-collector/otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml:ro
    ports:
      - "127.0.0.1:4318:4318"   # Only localhost — Django pushes here
      - "127.0.0.1:4317:4317"   # gRPC (optional)
      - "0.0.0.0:8889:8889"     # Open — Prometheus scrapes this
```

Run with:
```bash
docker compose -f docker-compose.otel.yml up -d
```

---

## 2. OTEL Collector Config Changes

**File:** `docker/otel-collector/otel-collector-config.yaml`

```diff
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
    postgresql:
-     endpoint: postgres:5432
+     endpoint: host.docker.internal:5432
      transport: tcp
      username: django
      password: django
      ...

  exporters:
    otlp/tempo:
-     endpoint: tempo:4317
+     endpoint: <TEMPO_SERVER_IP>:4317
      tls:
        insecure: true
```

> Replace `<TEMPO_SERVER_IP>` with the actual private IP of the Tempo server.
> To keep staging simple: remove the `postgresql` receiver entirely for now.

---

## 3. Tempo Config Changes

**File:** `docker/tempo/tempo-config.yaml`

```yaml
# ✅ NO CHANGES NEEDED
# Tempo listens on 0.0.0.0:4317 (receive traces)
# and 0.0.0.0:3200 (serve query API to Grafana)
# It does not call out to any other service.
```

Only ensure: **port `4317` is open** on the Tempo server firewall to accept from App Server IP.

---

## 4. Prometheus Config Changes

**File:** `docker/prometheus/prometheus.yml`

```diff
  scrape_configs:
    - job_name: "otel-collector-spanmetrics"
      scrape_interval: 10s
      scrape_timeout: 10s
      static_configs:
-       - targets: ["otel-collector:8889"]
+       - targets: ["<APP_SERVER_IP>:8889"]
          labels:
            source: "apm-spans"
```

> **Port `8889`** must be open on the App Server firewall, accessible from Prometheus Server IP only.

---

## 5. Grafana Datasource Changes

**File:** `docker/grafana/provisioning/datasources/datasources.yaml`

```diff
  datasources:
    - name: Prometheus
      uid: prometheus
      type: prometheus
      access: proxy
-     url: http://prometheus:9090
+     url: http://<PROMETHEUS_SERVER_IP>:9090

    - name: Tempo
      uid: tempo
      type: tempo
      access: proxy
-     url: http://tempo:3200
+     url: http://<TEMPO_SERVER_IP>:3200
```

---

## Summary: All Changes at a Glance

| File | What Changes |
|------|-------------|
| `settings/staging.py` | Add `tracenest.init()` block after sentry |
| `.envs/.env` | Add `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` |
| `scripts/gunicorn_conf_staging.py` | New file with `preload_app = True` (matches production) |
| `docker-compose.otel.yml` | New file — OTEL Collector sidecar on App Server |
| `otel-collector-config.yaml` | `postgres:5432` → `host.docker.internal:5432` |
| `otel-collector-config.yaml` | `tempo:4317` → `<TEMPO_SERVER_IP>:4317` |
| `tempo-config.yaml` | ✅ No changes |
| `prometheus.yml` | `otel-collector:8889` → `<APP_SERVER_IP>:8889` |
| `datasources.yaml` | Prometheus + Tempo URLs → real server IPs |

---

## Firewall Ports to Open

| Port | On Server | From | Purpose |
|------|-----------|------|---------|
| `4318` | App Server | `127.0.0.1` only | Django → OTEL Collector |
| `8889` | App Server | Prometheus Server IP | Prometheus scrapes metrics |
| `4317` | Tempo Server | App Server IP | OTEL Collector → Tempo (traces) |
| `3200` | Tempo Server | Grafana Server IP | Grafana reads traces |
| `9090` | Prometheus Server | Grafana Server IP | Grafana reads metrics |
| `3000` | Grafana Server | Your browser / VPN | Grafana UI |

---

## Pre-Staging Checklist

- [ ] `pip install tracenest` on the App Server
- [ ] Add `tracenest` to requirements file
- [ ] Add `tracenest.init()` block to `staging.py`
- [ ] Add `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318` to `.envs/.env`
- [ ] Create `scripts/gunicorn_conf_staging.py` with `preload_app = True` (matches production)
- [ ] Create `docker-compose.otel.yml` and start the OTEL Collector sidecar
- [ ] Update `otel-collector-config.yaml`: fix `postgres` and `tempo` endpoints
- [ ] Update `prometheus.yml`: scrape `<APP_SERVER_IP>:8889`
- [ ] Update `datasources.yaml`: Prometheus and Tempo real IPs
- [ ] Open firewall ports (see table above)
- [ ] Deploy Obs servers first (Tempo, Prometheus, Grafana), then App Server
- [ ] Verify in Grafana: traces appear in Tempo, spanmetrics appear in Prometheus
