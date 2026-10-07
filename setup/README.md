# Observability Host Deployment (Tempo)

Minimal observability service running Grafana Tempo for distributed tracing ingestion and queries. Production configurations (ports, resource limits, storage) are defined directly in `docker-compose.yml`. No `.env` file is required.

## 1. Firewall / Security

Allow inbound traffic on this observability host:
- `4327/tcp` (OTLP gRPC) and `4318/tcp` (OTLP HTTP) from your collector / App Server IP (collector trace push).
- `3200/tcp` (Tempo HTTP query API) from your Grafana server / VPN.

## 2. Run

```bash
docker compose up -d
```

Verify:
```bash
curl -f http://localhost:3200/ready
```

## 3. Grafana Integration

Add the Tempo datasource in your existing Grafana:
- Type: `Tempo`
- URL: `http://<OBS_SERVER_IP>:3200`
- Import JSON dashboards from `docker/grafana/dashboards/`.
