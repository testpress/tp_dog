# Grafana Dashboards & Navigation Guide

This guide covers the **8 pre-provisioned Grafana APM dashboards** in the TraceNest observability stack, explaining **how each dashboard is structured, how to navigate between them, and how to execute end-to-end incident triage and root cause analysis**.

---

## 1. Dashboard Navigation Map

The TraceNest dashboard suite is organized hierarchically from high-level operational triage down to granular code and query execution:

```text
                     ┌─────────────────────────────────────────┐
                     │          Needs Attention                │
                     │  (Triage: Error Spikes & Slow Routes)   │
                     └────────────────────┬────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │           Service Catalog               │
                     │  (Global Health & Baseline Anomalies)   │
                     └────────────────────┬────────────────────┘
                                          │
         ┌────────────────────────────────┼────────────────────────────────┐
         ▼                                ▼                                ▼
┌─────────────────────────┐   ┌─────────────────────────┐   ┌─────────────────────────┐
│     Django Overview     │   │   PostgreSQL Overview   │   │     Redis Overview      │
│  (RPS, P95, 5xx Rates)  │   │  (QPS, Pool, Topology)  │   │ (Ops/sec, Hit Rate, Cmd)│
└───────────┬─────────────┘   └───────────┬─────────────┘   └───────────┬─────────────┘
            │                             │                             │
            ▼                             ▼                             ▼
┌─────────────────────────┐   ┌─────────────────────────┐   ┌─────────────────────────┐
│ Django Endpoint Details │   │ Postgres Query Details  │   │  Redis Command Details  │
│(Views, Middleware, Tmpl)│   │ (Sanitized SQL, Replica)│   │ (Pipelines, Arguments)  │
└───────────┬─────────────┘   └───────────┬─────────────┘   └───────────┬─────────────┘
            │                             │                             │
            └─────────────────────────────┼─────────────────────────────┘
                                          │
                                          ▼
                     ┌─────────────────────────────────────────┐
                     │              Grafana Tempo              │
                     │  (Trace Waterfall & Flamegraph Engine)  │
                     └─────────────────────────────────────────┘
```

---

## 2. Dashboard Catalog & Details

### 1. Needs Attention — Operational Issues Overview
* **UID**: `tracenest-needs-attention`
* **Purpose**: Primary incident triage board. Surfaces any service or endpoint that is currently failing SLOs or experiencing performance regressions.
* **Key Panels**:
  * **Severity Filter**: Filter by `Critical` (Error rate > 5%, P95 > 1s, RPS Anomaly > 200%), `Warning` (Error rate > 1%, P95 > 250ms, RPS Anomaly > 50%), or `Info`.
  * **Active Operational Issues Table**: Lists failing endpoints, current error percentage, P95 latency, and deviation from historical baseline.
  * **Quick-Drilldown Action Links**: Direct links to open the affected endpoint in **Django Endpoint Details** or inspect raw error traces in **Tempo**.

---

### 2. Service Catalog & Global Health
* **UID**: `tracenest-project-catalog`
* **Purpose**: Single-pane-of-glass overview across all microservices and background workers.
* **Key Panels**:
  * **Global Service Grid**: High-level health cards displaying RPS, Error Rate %, and P95 latency per service.
  * **Baseline vs Current Traffic Anomaly Detection**:
    * Compares current 5-minute request rate against the **rolling 7-day median baseline**:
      $$\text{Anomaly \%} = \frac{\text{Current RPS} - \text{7-Day Baseline}}{\text{7-Day Baseline}} \times 100$$
    * Identifies unexpected traffic surges (potential DDoS, scraper, or retry storm) or traffic drops (upstream network failure).
  * **Global RPS Anomaly List**: Drilldown table highlighting anomalous endpoints with links to deeper telemetry.

---

### 3. Django Overview
* **UID**: `tracenest-django-overview`
* **Purpose**: Service-level overview for Django applications.
* **Key Panels**:
  * **Throughput (RPS)**: Inbound request rate grouped by HTTP status code (`2xx`, `3xx`, `4xx`, `5xx`).
  * **Latency Percentiles**: P50, P90, P95, and P99 response time trends over time.
  * **Error Rate %**: Ratio of failed requests with error budget indicators.
  * **Slowest Endpoints Table**: Ranked table of normalized routes (`/api/products/{id}/`) by P95 duration.

---

### 4. Django Endpoint Details
* **UID**: `tracenest-django-endpoint`
* **Purpose**: Deep-dive into a single endpoint route.
* **Key Panels**:
  * **Endpoint RED Metrics**: Route-specific RPS, error rate, and duration percentiles.
  * **Middleware Execution Breakdown**: Wall-clock time spent in `SecurityMiddleware`, `AuthenticationMiddleware`, `SessionMiddleware`, etc.
  * **View Execution Time**: Exact duration of the view function or DRF ViewSet action.
  * **Template Rendering Breakdown**: Time spent evaluating templates and nested `{% include %}` tags.
  * **Recent Traces (Tempo)**: Direct list of recent traces for this route with instant waterfall access.

---

### 5. PostgreSQL Overview
* **UID**: `tracenest-postgres-overview`
* **Purpose**: Topology-aware database monitoring.
* **Key Panels**:
  * **Total Query Throughput (QPS)**: Overall query rate across all database connections.
  * **Primary vs Read-Replica Traffic Split**: Ratio of queries executed on Primary (`master`) vs Read-Replicas (`replica1`, `replica2`).
  * **PgBouncer Pool Contention**:
    * Active client connections vs waiting client queries in pool queues.
    * Allows engineers to immediately distinguish between **pool queueing delay** and **database engine execution time**.
  * **Top 10 Slowest SQL Queries**: Sanitized query statements ranked by total execution time.

---

### 6. PostgreSQL Query Details
* **UID**: `tracenest-postgres-query`
* **Purpose**: Deep performance analysis of a specific SQL query pattern.
* **Key Panels**:
  * **Sanitized Query Pattern**: Full normalized query text with literals parameterized (`WHERE id = %s`).
  * **Execution Rate & Latency Percentiles**: P50/P95 execution duration for this specific query.
  * **Originating HTTP Endpoints**: Which web routes execute this query most frequently.
  * **Associated Trace Exemplars**: Clickable trace links to see the exact application context where the query executed.

---

### 7. Redis Overview & Command Details
* **UIDs**: `tracenest-redis-overview`, `tracenest-redis-command`
* **Purpose**: Cache performance, command timing, and pipeline diagnostics.
* **Key Panels**:
  * **Commands per Second**: Command volume partitioned by command name (`GET`, `SET`, `HGETALL`, `INCR`).
  * **Cache Hit vs Miss Ratio**: Efficiency gauge for cache-backed views.
  * **Command Duration Percentiles**: P95 latency per Redis command.
  * **Pipeline Operations**: Batch pipeline frequency and average pipeline size.

---

## 3. Operational Workflows & Triage Playbooks

### Workflow 1: Triage an Incident (Spike to Root Cause)

```text
[1. Incident Alert / Needs Attention]
        │  Engineer spots high error rate or latency on /api/orders/
        ▼
[2. Django Endpoint Details]
        │  Opens endpoint dashboard; observes template & DB time are normal,
        │  but downstream HTTP call to inventory service is failing.
        ▼
[3. Click Trace Link / Exemplar]
        │  Opens Tempo Waterfall.
        ▼
[4. Tempo Trace Waterfall]
        │  Locates failed span: 🌐 HTTP GET https://inventory.internal/stock (504 Gateway Timeout)
        ▼
[5. Root Cause Isolated]
```

---

### Workflow 2: Click-to-Trace via Prometheus Exemplars

When viewing any Latency or Duration panel in Grafana:
1. Look for **blue diamonds / dots** hovering above the metric line.
2. Hover over a dot to see the attached metadata (`trace_id`, `duration`).
3. Click the `trace_id` link in the popup.
4. Grafana opens a split-screen or navigates directly to the **Tempo trace waterfall**, showing the exact execution tree for that measurement.

```text
Latency Graph (Prometheus)
  │
  ├───────◆ (Exemplar: trace_id=4bf92f3577b34da6...) ──► [ Click ]
  │                                                          │
  └──────────────────────────────────────────────────────────┼───────────────► Tempo Waterfall
                                                                               ├── django.request
                                                                               └── 🐘 SELECT * FROM items (Slow)
```

---

### Workflow 3: Diagnosing Slow Database Calls (PgBouncer vs Engine)

If database latency spikes:
1. Open **PostgreSQL Overview** (`tracenest-postgres-overview`).
2. Check the **PgBouncer Connection Pool** panel:
   - If `Waiting Clients > 0` and `Active Server Connections` is saturated at 100%: **The bottleneck is pool connection exhaustion**, not a slow database engine.
   - If `Waiting Clients == 0` but `P95 Query Duration` is high: **The query itself is inefficient** (missing index or table scan).
3. Click into **PostgreSQL Query Details** to inspect the sanitized query and find which Django views trigger it.

---

## 4. Common Variables & Dashboard Controls

Every dashboard provides a standardized top toolbar for filtering telemetry:

| Variable | Description | Example Values |
| :--- | :--- | :--- |
| **`$project`** | Selects the target service/application | `otel-sample`, `django-lite-app` |
| **`$cluster`** | Selects the environment/cluster | `production`, `demo-cluster` |
| **`$endpoint`** | Filters by normalized endpoint path | `/api/products/{id}/`, `/api/checkout/` |
| **`$http_method`** | Filters by HTTP verb | `GET`, `POST`, `PUT`, `DELETE` |
| **`$instance`** | Filters by database/cache host instance | `pgbouncer`, `slave1db`, `redis` |
| **`$query`** | Filters by normalized SQL query pattern | `SELECT api_product...` |
| **`$command`** | Filters by Redis command name | `GET`, `SET`, `HGETALL` |
| **`$severity`** | Filters issues on the triage board | `Critical`, `Warning`, `Info` |
| **`$Filters`** | Ad-hoc filter bar for adding custom label matchers | `status_code = 500`, `error = true` |
| **Time Range** | Time window for PromQL aggregation | `Last 15 minutes`, `Last 1 hour`, `Last 24 hours` |

---

## 5. Summary Cheat Sheet

* **Start here for incidents**: [`tracenest-needs-attention`](../docker/grafana/dashboards/tracenest_needs_attention.json)
* **Start here for service health**: [`tracenest-service-catalog`](../docker/grafana/dashboards/tracenest_service_catalog.json)
* **Start here for route latency**: [`tracenest-django-endpoint-details`](../docker/grafana/dashboards/tracenest_django_endpoint_details.json)
* **Start here for database latency**: [`tracenest-postgres-overview`](../docker/grafana/dashboards/tracenest_postgres_overview.json)
