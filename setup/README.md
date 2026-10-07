# Observability Host Deployment

Minimal observability stack running Tempo (traces) and Prometheus (metrics). All production configurations (ports, limits, storage retention) are hardcoded directly in `docker-compose.yml`. No `.env` file is required.

## 1. Configure Target App Host

Edit `prometheus/targets/otel-collector.prod.json`:
- Replace `<APP_SERVER_HOST>` with the app server's IP (e.g. `65.21.236.56`).
- Set `"env"` to `staging` or `production`.

## 2. Firewall / Security

Allow inbound traffic on this observability host:
- `4327/tcp` (OTLP gRPC) from your collector / App Server IP (collector trace push).
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
