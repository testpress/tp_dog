# Production Deployment

Split deployment for TraceNest: the application, SDK and OpenTelemetry Collector
run on the **app server**; Tempo, Prometheus and Grafana run on a separate
**observability server**.

---

## 1. Topology

Only two links cross the server boundary.

```text
  APP SERVER                              OBSERVABILITY SERVER
  ──────────                              ────────────────────
  django app (gunicorn)
    └── TraceNest SDK
          │ OTLP/HTTP :4318   (localhost, never crosses the network)
          ▼
  otel-collector
    ├── :8889 ──── Prometheus SCRAPES this (pull) ───────────▶ prometheus
    └── :4317 ──── collector PUSHES traces here ───────────▶ tempo
                                                           ├── :3200 ──▶ grafana
                                                           └── :9090 ──▶ grafana ◀── :9090
```

| Component | Runs on | Why there |
| --- | --- | --- |
| Django app + TraceNest SDK | App server | — |
| OTel Collector | App server | Keeps the SDK→collector hop local, and only one link (collector→Tempo) leaves the host |
| Tempo | Obs server | 14d trace retention, long-lived data |
| Prometheus | Obs server | Long-lived TSDB, queried by Grafana |
| Grafana | Obs server | Queries both datasources over the local Docker network |

**Do not run Grafana on the app server.** It would need both datasource URLs
repointed at the remote host for no benefit.

---

## 2. The URLs you must change

Running `docker compose up` and then splitting the stack onto two machines
breaks exactly three hard-coded addresses. Everything else resolves already.

| # | Direction | What | Where | Change to |
| --- | --- | --- | --- | --- |
| 1 | push | Collector → Tempo | `docker/otel-collector/otel-collector-config.yaml` (`otlp/tempo.endpoint`) | `tempo.obs.internal:4317` |
| 2 | pull | Prometheus → Collector | `docker/prometheus/targets/otel-collector.prod.json` | `<app-server-host>:8889` |
| 3 | — | Tempo OTLP ports | `docker-compose.yml` (tempo published only `3200`) | publish `4317` + `4318` |

Items 1 and 2 are now environment/data driven — see §4. Item 3 is handled by
`docker-compose.obs.yml`.

### URLs that must NOT change

| URL | Why it is fine |
| --- | --- |
| SDK `OTEL_EXPORTER_OTLP_ENDPOINT` → collector | Stays on the app server, resolved over local Docker DNS |
| Grafana → `http://prometheus:9090` | Both on the obs server, shared Docker network |
| Grafana → `http://tempo:3200` | Both on the obs server, shared Docker network |
| Collector → `postgres:5432` | Both on the app server |

---

## 3. Why Tempo's ports were the trap

`tempo-config.yaml` already listened on `0.0.0.0:4317`, but the container never
published that port to the host. In the single-host stack the collector reached
it over the Docker bridge network, so this was invisible. Once Tempo moves to
another machine, the receiver is genuinely unreachable and the collector's
retries silently discard spans.

Symptom: `app_*` metrics stay live in Grafana (Prometheus→collector still works)
but every waterfall lookup returns `Trace not found`. Check the collector log
for `exporting failed` / connection refused against the Tempo host.

---

## 4. Environment variables

### App server

```bash
cp .env.app.example .env
```

```dotenv
# .env.app.example
TEMPO_OTLP_ENDPOINT=tempo.obs.internal:4317
TEMPO_OTLP_INSECURE=true
```

`TEMPO_OTLP_ENDPOINT` must be resolvable **from the app host**. Do not put the
Docker service name `tempo` here — it only resolves on a shared Docker network.

The collector fails fast at startup if `TEMPO_OTLP_ENDPOINT` is unset, rather
than booting and discarding every span:

```
Error: exporters::otlp/tempo: requires a non-empty "endpoint"
```

### Observability server

```bash
cp .env.obs.example .env
```

```dotenv
# .env.obs.example
TEMPO_OTLP_GRPC_PORT=4317
TEMPO_OTLP_HTTP_PORT=4318
TEMPO_QUERY_PORT=3200
PROMETHEUS_PORT=9090
PROMETHEUS_RETENTION=15d
GRAFANA_PORT=3000
GF_AUTH_DISABLE_LOGIN_FORM=false
GF_SECURITY_ADMIN_PASSWORD=<set-a-real-password>
```

`GF_SECURITY_ADMIN_PASSWORD` is mandatory — the compose file refuses to
interpolate without it. `.env` is gitignored; only the `*.example` templates
are tracked.

---

## 5. Deploy

### Observability server first

```bash
cp .env.obs.example .env && $EDITOR .env
docker compose -f docker-compose.obs.yml up -d
curl -fsS http://localhost:3200/ready
```

### Then the app server

```bash
cp .env.app.example .env && $EDITOR .env
docker compose up -d
```

### Edit the Prometheus target

`docker/prometheus/targets/otel-collector.prod.json` ships with the placeholder
`app-server.internal:8889`. Replace it with the real app host — it is data, not
config, so it is not env-driven:

```json
[
  {
    "targets": ["app-prod-01.example.com:8889"],
    "labels": { "source": "apm-spans" }
  }
]
```

Prometheus re-reads it within `refresh_interval: 30s`; no restart needed.

---

## 6. Firewall

| Host | Port | Direction | Peer |
| --- | --- | --- | --- |
| Obs | `4317/tcp` | inbound | App server only |
| Obs | `3200/tcp` | inbound | Grafana, operators |
| Obs | `9090/tcp` | inbound | Grafana, operators |
| Obs | `3000/tcp` | inbound | Operators |
| App | `8889/tcp` | inbound | Obs server only |

Do not expose `8889`, `4317` or `4318` to `0.0.0.0` on the public internet.
`8889` exposes raw span-derived metrics with no authentication, and Tempo's
`4317` accepts unauthenticated trace writes.

---

## 7. Security

`TEMPO_OTLP_INSECURE=true` means plaintext gRPC. That is acceptable only over a
trusted network segment. Before crossing an untrusted one:

1. Terminate TLS at Tempo (or in front of it) with real certificates.
2. Set `TEMPO_OTLP_INSECURE=false` on the app server.
3. Add `tls:` `ca_file:` / `cert_file:` / `key_file:` to `otlp/tempo` in the
   collector config.

There is no authentication on the OTLP receivers or on Tempo's query API today.
Grafana is set to anonymous-Admin in the example `.env`; set
`GF_AUTH_DISABLE_LOGIN_FORM=true` once a real admin password exists.

---

## 8. Verify

```bash
# Obs server — Tempo ready
curl -fsS http://localhost:3200/ready

# Obs server — Prometheus sees the app server's collector (expect up=1)
curl -s http://localhost:9090/api/v1/targets \
  | python3 -c 'import sys,json;[print(t["health"],t["scrapeUrl"]) for t in json.load(sys.stdin)["data"]["activeTargets"]]'

# Obs server — metrics arriving
curl -s 'http://localhost:9090/api/v1/query?query=up' | python3 -m json.tool

# App server — collector healthy and not erroring on the Tempo push
docker compose logs otel-collector | grep -iE 'error|exporting failed' | tail

# App server — SDK is actually exporting
curl -s localhost:8001/api/products/ -o /dev/null -D - | grep -i x-trace-id
```

Then confirm the trace ID returned by the app shows up in Grafana at
`http://<obs-host>:3000`.

---

## 9. Notes on the changes

- The collector's `otlp/tempo` exporter gained `timeout: 10s`,
  `retry_on_failure` (1s → 30s, 5 min elapsed cap) and a `5000` queue. A
  wide-area link needs more slack than the Docker bridge did; without retry,
  a brief Tempo blip drops spans permanently.
- `prometheus.yml` now uses `file_sd_configs` because Prometheus does **not**
  expand environment variables in its config file. Targets moved to
  `docker/prometheus/targets/*.json`.
- The dev stack mounts exactly one targets file (not the directory) so the
  `*.json` glob cannot also pick up `otel-collector.prod.json` and register a
  duplicate target for the same job.
- `PROMETHEUS_RETENTION` is now explicit (default `15d`) rather than unbounded.
  Keep it at or below Tempo's `block_retention: 336h` (14d) so metrics and
  traces cover the same window.
- Tempo, Prometheus and Grafana gained `restart: unless-stopped` and
  healthchecks in the production compose only; the dev stack is unchanged.

## 10. Known production gaps

Not addressed here, worth tracking before real traffic:

- **No authentication** on any OTLP receiver or on Tempo's query API.
- **Single collector, single-node Tempo.** No collector HA, no object storage
  backend, no ingesters/queriers split.
- **No `metrics_generator`** in Tempo, so Grafana "span metrics" views are
  served by Prometheus rather than by Tempo.
- **Unbounded Prometheus memory** — TSDB retention is capped by time, not size.
- **No k8s manifests.** Docker Compose assumes a host with fixed addresses; a
  pod network needs different DNS, so the collector endpoint and Prometheus
  target would move to Service DNS names.