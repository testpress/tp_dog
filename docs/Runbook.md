# Operational Runbook

Production operations and incident triage guide for tp_dog APM.

---

### 1. Is telemetry flowing?
```bash
curl -s http://<collector>:8889/metrics | grep apm_calls_total
```
*If counter is incrementing, spans are reaching the collector and metrics are being derived.*

### 2. How do I disable tp_dog on one server?
```bash
TP_DOG_DISABLED=true
```
*Set in environment, systemd unit, or container env and reload the app process.*

### 3. How do I enable debug logging?
```bash
TP_DOG_DEBUG=true
```
*Emits verbose span creation and export logs to the `tp_dog.*` logger namespace only.*

### 4. Is the Collector dropping spans?
```bash
curl -s http://<collector>:8888/metrics | grep -E "otelcol_processor_dropped|otelcol_exporter_enqueue_failed"
```
*Non-zero values indicate memory pressure, queue overflow, or downstream storage outage.*

### 5. How do I throttle sampling under load?
```bash
TP_DOG_SAMPLE_RATE=0.1
```
*Reduces head-based sampling to 10% (accepted values: `0.0` to `1.0`). Reload app process.*

### 6. Are Tempo and Prometheus healthy?
```bash
curl -f http://<tempo>:3200/ready                  # Must return "ready"
curl -s http://<prometheus>:9090/api/v1/targets   # otel-collector target must show "health":"up"
```

---

## Emergency Killswitch

To immediately disable all tracing overhead across an instance:
```bash
export TP_DOG_DISABLED=true
sudo systemctl restart <app-service>
```

---

## Configuration Reference

| Variable | Default | Purpose |
|---|---|---|
| `TP_DOG_DISABLED` | `false` | Master killswitch (`true` shuts down all SDK tracing). |
| `TP_DOG_SAMPLE_RATE` | `1.0` | Head sampling ratio (`0.0` to `1.0`). |
| `TP_DOG_ENDPOINT` | `http://localhost:4318` | Collector OTLP/HTTP endpoint. |
| `TP_DOG_DEBUG` | `false` | Verbose SDK diagnostic logging. |
| `TP_DOG_IGNORE_ENDPOINTS` | `""` | Comma-separated regex routes to skip. |
| `TP_DOG_TAGS` | `""` | Global tags appended to all spans (`k1=v1,k2=v2`). |
| `TP_DOG_DB_ROLE_MAP` | `""` | JSON or alias map for DB primary/replica routing. |
