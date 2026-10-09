# Architecture & System Design

This document explains how the **tp_trace Observability Stack** is arranged, how data flows between components, and how each service in the Docker Compose environment is configured.

---

## 1. High-Level System Architecture

The observability stack consists of four core infrastructure components working together:

```text
 ┌───────────────────────────┐
 │   Application (SDK)       │
 │   (Emits OTLP Spans)      │
 └─────────────┬─────────────┘
               │
               │ HTTP POST /v1/traces (Port 4318)
               ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                 OpenTelemetry Collector                     │
 │                                                             │
 │   1. Receives OTLP Traces                                   │
 │   2. Batches spans (`batch` processor)                      │
 │   3. Generates RED metrics (`spanmetrics` connector)        │
 └──────────────┬───────────────────────────────┬──────────────┘
                │                               │
                │ OTLP / gRPC (Port 4317)       │ Prometheus Scrape (Port 8889)
                ▼                               ▼
 ┌───────────────────────────┐   ┌───────────────────────────┐
 │       Grafana Tempo       │   │        Prometheus         │
 │      (Trace Storage)      │   │     (Metrics Storage)     │
 │  Stores raw spans & DAGs  │   │ Scrapes metrics every 10s │
 └─────────────┬─────────────┘   └──────────────┬────────────┘
               │                                │
               │ HTTP Query (Port 3200)         │ PromQL (Port 9090)
               └────────────────┬───────────────┘
                                │
                                ▼
                 ┌─────────────────────────────┐
                 │       Grafana APM UI        │
                 │   Dashboards & Flamegraphs  │
                 │         (Port 3000)         │
                 └─────────────────────────────┘
```

---

## 2. Component Breakdown

| Service | Image | Role | Inbound Ports | Outbound Destinations |
| :--- | :--- | :--- | :--- | :--- |
| **OpenTelemetry Collector** | `otel/opentelemetry-collector-contrib:0.96.0` | Ingestion, processing, and metrics generation | `4317` (gRPC)<br>`4318` (HTTP)<br>`8889` (Metrics) | `tempo:4317` (Traces)<br>Prometheus scrapes `:8889` |
| **Grafana Tempo** | `grafana/tempo:2.4.1` | Distributed trace storage and TraceQL query engine | `4317` (gRPC OTLP)<br>`3200` (HTTP API) | Local disk (`/var/tempo`) |
| **Prometheus** | `prom/prometheus:v2.51.0` | Time-series database for throughput, latency, and errors | `9090` (Web UI & API) | Scrapes `otel-collector:8889` |
| **Grafana** | `grafana/grafana:12.4.10` | Visualization, APM dashboards, and waterfall flamegraphs | `3000` (Web UI) | `prometheus:9090`<br>`tempo:3200` |

---

## 3. How Each Service Works

### 1. OpenTelemetry Collector (`otel-collector`)

The OTel Collector is the central nervous system of the observability pipeline. It receives telemetry from the application, processes it, and routes it to storage backends.

```text
                       OTEL COLLECTOR PIPELINES
 ─────────────────────────────────────────────────────────────────────────────
 TRACES PIPELINE:
   [OTLP HTTP :4318] ──► [Batch Processor] ──┬──► [OTLP Exporter] ──► Tempo (:4317)
                                              └──► [Spanmetrics Connector]
                                                           │
 METRICS PIPELINE:                                         ▼
   [Spanmetrics / PostgreSQL] ──► [Batch Processor] ──► [Prometheus Exporter :8889]
 ─────────────────────────────────────────────────────────────────────────────
```

- **Receivers**:
  - `otlp/http` (port `4318`): Receives JSON/Protobuf trace payloads from Python applications.
  - `otlp/grpc` (port `4317`): Receives gRPC trace payloads.
  - `postgresql` (port `5432`): Scrapes PostgreSQL database engine metrics every 10s.
- **Processors**:
  - `batch`: Buffers telemetry in memory (1-second timeout or 256 items) to minimize network overhead and maximize throughput.
- **Connectors (`spanmetrics`)**:
  - Automatically derives RED metrics (**R**ate, **E**rrors, **D**uration) directly from raw trace spans in real time.
  - **Key Benefit**: Keeps the application SDK lightweight and fast — apps only emit spans, while the collector generates the metrics without extra client-side CPU or memory overhead.
  - Generates explicit latency histogram buckets (`2ms`, `5ms`, `10ms`, `25ms`, `50ms`, `100ms`, `250ms`, `500ms`, `1s`, `2.5s`, `5s`, `10s`).
  - Preserves critical high-value dimensions like `http.method`, `http.status_code`, `http.route`, `db.system`, `db.operation`, `db.instance`, `db.statement`, `server.address`, `server.port`, `error`, and `span.type`.
- **Exporters**:
  - `otlp/tempo`: Sends complete traces via gRPC to Tempo (`tempo:4317`).
  - `prometheus`: Exposes derived spanmetrics on `0.0.0.0:8889` under the `apm_` namespace (e.g. `apm_calls_total`, `apm_duration_milliseconds_bucket`).

---

### 2. Grafana Tempo (`tempo`)

Tempo is a high-volume, cost-effective distributed tracing backend.

- **Trace Ingestion**: Listens on port `4317` for OTLP spans forwarded by the collector.
- **Storage Strategy**:
  - Writes active blocks to `/var/tempo/wal` (Write-Ahead Log) and `/var/tempo/traces`.
  - Rolls blocks every 5 minutes (`max_block_duration: 5m`).
  - Automatically compacts and retains traces for 14 days (`block_retention: 336h`).
- **Querying**:
  - Exposes an HTTP query API on port `3200`.
  - Supports **TraceQL** search (e.g. `{ span.http.route = "/api/orders/" && duration > 500ms }`).
  - Serves full span trees and waterfall timelines directly to Grafana.

---

### 3. Prometheus (`prometheus`)

Prometheus stores numerical time-series metrics used for graphs, rate calculations, and alerts.

- **Scrape Configuration**:
  - Scrapes the OTel Collector endpoint (`otel-collector:8889`) every **10 seconds**.
- **PromQL Metrics Stored**:
  - `apm_calls_total`: Request counters partitioned by service, route, status code, error state, and database system (PgBouncer is distinguished via span name prefix `🔵` and `server.address`/`db.system`).
  - `apm_duration_milliseconds_bucket`: Latency histogram buckets for computing P50, P90, P95, and P99 percentiles via `histogram_quantile()`.
  - `postgresql_*`: Database engine metrics (locks, operations, connections).
- **Baseline Calculations**:
  - Evaluates rolling baseline rules (e.g., 7-day median comparison) to detect throughput anomalies.

---

### 4. Grafana (`grafana`)

Grafana is the unified user interface for exploring metrics, searching traces, and diagnosing bottlenecks.

- **Datasource Connections**:
  1. **Prometheus Datasource (`http://prometheus:9090`)**:
     - Powers time-series panels (RPS, Latency percentiles, Error rates).
     - Configured with **Exemplar linking** (`trace_id` -> `tempo`), so clicking a metric spike immediately opens the exact Trace waterfall in Tempo.
  2. **Tempo Datasource (`http://tempo:3200`)**:
     - Powers the trace search and waterfall flamegraph viewer.
     - Configured with **Traces to Metrics** links and **Service Map / Node Graph** visualization.
- **Pre-provisioned Dashboards**:
  - **Service Catalog**: High-level health and throughput across all components.
  - **Django Overview & Endpoint Details**: Endpoint latency percentiles, error rates, and route waterfalls.
  - **PostgreSQL Overview & Query Details**: Sanitized SQL queries, query volume, primary vs replica routing, and execution times.
  - **Redis Overview & Command Details**: Cache hit rates, command latencies (`GET`, `SET`, `INCR`), and pipeline timing.
  - **Needs Attention**: Highlights slow endpoints, error surges, and anomalous traffic spikes.

---

## 4. End-to-End Request Journey

Here is the exact lifecycle of telemetry during an HTTP request:

```text
1. Client sends HTTP GET /api/products/123/ to Django application.
2. tp_trace SDK:
   - Starts root span `django.request` and normalizes route to `/api/products/{id}/`.
   - Starts child spans for Middleware, Views, Postgres SQL, Redis cache, and Templates.
   - Wraps and closes all spans upon response.
3. tp_trace SDK batches spans and sends via HTTP POST to `http://otel-collector:4318/v1/traces`.
4. OTel Collector:
   - Passes spans to `spanmetrics` connector -> updates `apm_calls_total` and latency histograms.
   - Forwards raw spans to `tempo:4317`.
5. Prometheus scrapes `otel-collector:8889` every 10 seconds.
6. Engineer opens Grafana at `http://localhost:3000`:
   - Prometheus data renders live RPS, P95 latency, and error rate graphs.
   - Clicking an endpoint or trace link opens the Tempo waterfall showing exact line-by-line execution times.
```

In a split production deployment the collector and Tempo sit on different hosts,
so steps 4-5 become network links. See [Deployment.md](Deployment.md).

---

## 5. Docker Network & Port Mapping

All observability containers communicate internally over the dedicated Docker bridge network: **`tp-observability-net`**.

```text
Host Port     Container Port     Target Service      Description
─────────     ──────────────     ──────────────      ───────────
3000          3000               grafana             Grafana Web UI
9090          9090               prometheus          Prometheus Web UI & API
3200          3200               tempo               Tempo Query HTTP API
4317          4317               otel-collector      OTLP gRPC Receiver
4318          4318               otel-collector      OTLP HTTP Receiver
8889          8889               otel-collector      Prometheus Metrics Exporter
```

When Tempo, Prometheus and Grafana are hosted on a separate observability
server, Tempo's OTLP receivers (`4317`/`4318`) must additionally be published
on that host so the remote collector can push to it. They are not published by
the single-host stack above, because the collector reaches Tempo over the
Docker bridge. See [Deployment.md](Deployment.md) §3.

---
