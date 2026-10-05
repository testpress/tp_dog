# Observability Host Deployment

Minimal observability stack running Tempo (traces) and Prometheus (metrics).

## 1. Configure Target App Host

Edit:

`prometheus/targets/otel-collector.prod.json`

- Replace `<APP_SERVER_HOST>` with the app server's private IP or internal DNS.
- Set `env` to `staging` or `production`.

## 2. Environment Settings (Optional)

Create `.env` to override defaults:

```bash
cp .env.example .env
```


Default retentions and ports:
- `TEMPO_OTLP_GRPC_PORT=4317`
- `PROMETHEUS_PORT=9090`
- `PROMETHEUS_RETENTION_TIME=15d`
- `PROMETHEUS_RETENTION_SIZE=20GB`

## 3. Firewall / Security

Allow inbound traffic on this observability host:
- `4317/tcp` (OTLP gRPC) from your App Server IP only (collector trace push).
- `9090/tcp` (Prometheus) from your Grafana server / VPN.
- `3200/tcp` (Tempo query) from your Grafana server / VPN.

Allow inbound traffic on your App Server:
- `8889/tcp` and `8888/tcp` from this Observability Host IP only (Prometheus scrape).

## 4. Run

```bash
docker compose up -d
```

Verify:
```bash
curl -f http://localhost:3200/ready
curl -s http://localhost:9090/-/healthy
curl -s http://localhost:9090/api/v1/targets
```

## 5. Grafana Integration (Later)

Add datasources in your existing Grafana:
- Prometheus URL: `http://<OBS_SERVER_IP>:9090`
- Tempo URL: `http://<OBS_SERVER_IP>:3200`
- Import JSON dashboards from `docker/grafana/dashboards/`.
