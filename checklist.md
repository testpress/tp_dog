# tp_trace SDK v3 — Observability Requirements Checklist

This document tracks the status of all application-level, service-level, and request-level observability requirements specified for **tp_trace SDK v3**.

## Summary Table

| Requirement Category | Total Items | Done | Status |
| :--- | :---: | :---: | :---: |
| [1. Core Metrics and Visibility](#1-core-metrics-and-visibility) | 7 | 7 | ✅ Complete |
| [2. Application and Service Dashboard](#2-application-and-service-dashboard) | 3 | 3 | ✅ Complete |
| [3. Service-Level Details](#3-service-level-details) | 4 | 4 | ✅ Complete |
| [4. Endpoint and Operation Instances](#4-endpoint-and-operation-instances) | 2 | 2 | ✅ Complete |
| [5. Request Trace and Waterfall](#5-request-trace-and-waterfall) | 2 | 2 | ✅ Complete |
| [6. Service Performance Over Time](#6-service-performance-over-time) | 2 | 2 | ✅ Complete |
| [7. Time-Period Comparison](#7-time-period-comparison) | 2 | 2 | ✅ Complete |
| [8. Sampling and Retention](#8-sampling-and-retention) | 2 | 2 | ℹ️ SDK Implemented (Policy Managed at Backend) |
| [9. Overall User Flow](#9-overall-user-flow) | 1 | 1 | ✅ Complete |

---

## Detailed Requirements Breakdown

### 1. Core Metrics and Visibility

- [x] **Application & service-level latency, throughput, and error metrics**
  - *Details*: RED metrics (`apm_calls_total`, `apm_duration_milliseconds_bucket`) automatically derived from SDK spans via the OpenTelemetry Collector's `spanmetrics` connector and exported to Prometheus.
- [x] **Endpoint-level request volume, throughput, latency, and error analysis**
  - *Details*: Low-cardinality URL route normalization (`_normalize_route`) in Django integration (`src/tp_trace/integrations/django/request.py`), aggregated per endpoint path in Prometheus and displayed in the generic service/operation dashboards (`$service=django`, `$operation=GET /api/products/…`).
- [x] **Visibility into key services**:
  - [x] **Django application server**: Request handlers (`django.request`), middleware (`django.middleware.*`), views (`🐍 django.view.*`), templates (`🎨 django.template:*`), cache operations (`django.cache.*`), and auth events (`🔐 django.auth.*`).
  - [x] **PostgreSQL primary database**: Driver cursor wrapping, sanitized SQL queries, `db.role="primary"`, PgBouncer connection pool topology detection (`🔵`).
  - [x] **PostgreSQL replica databases**: Replica server topology detection (`postgres-replica1`, `postgres-replica2`, `slave1`, `slave2`) with `db.role="replica"` tagging (`🐘`).
  - [x] **Redis**: Command formatting (`GET`, `SET`, `INCR`), pipeline execution tracing, sensitive argument redacting (`AUTH`, `CONFIG`), and IP address masking (`🔴`).
  - [x] **External APIs**: Outgoing HTTP request tracing (`requests` library integration) producing `🌐 HTTP <METHOD> <HOST>` client spans with full URL and status codes.
- [x] **Distributed tracing for individual requests**
  - *Details*: W3C `traceparent` header propagation across incoming HTTP headers, root `SERVER` span `django.request`, child span context propagation, and `X-Trace-ID`/`X-Span-ID` response headers.
- [x] **Span waterfall visualization for individual requests**
  - *Details*: Grafana Tempo datasource rendering complete nested execution trees (middleware, views, templates, SQL queries, Redis ops, HTTP calls).
- [x] **Filtering by application, service, endpoint, environment, status, and time range**
  - *Details*: Grafana dashboard template variables (`$project`, `$cluster`, `$service`, `$operation`, `$severity`, `$Filters`) + Grafana native time-range picker.
- [x] **Performance comparison across different time periods**
  - *Details*: 7-day median rolling baseline queries (`quantile_over_time(0.5, apm_calls_total[7d:1h])`), current 5m rate/latency comparison, deviation percentage calculation, and anomaly detection PromQL expressions embedded directly in Grafana dashboards (`tp_trace_service_catalog.json`).

---

### 2. Application and Service Dashboard

- [x] **Main dashboard providing an overview of overall application & per-service performance**
  - *Details*: `docker/grafana/dashboards/tp_trace_service_catalog.json` ("tp_trace APM — Service Catalog").
- [x] **Per-service metrics summary table** (Service | Request Rate | Latency | Error Rate)
  - *Details*: "Installed Components Matrix" and "Throughput Across Components" panels in `tp_trace_service_catalog.json` covering Django, Postgres Primary, Postgres Replicas, Redis, and External APIs.
- [x] **Easy identification of bottleneck / service causing performance degradation**
  - *Details*: "% Time Spent by Downstream Service" breakdown panel, RPS anomaly alerts, and cross-service latency metrics.

---

### 3. Service-Level Details

- [x] **Dedicated service pages accessible via service selection**
  - *Details*: Interactive links from the Service Catalog dashboard (`tp_trace-project-catalog`) open `/d/generic-service-overview?var-service=<service>`. The six former component-specific dashboards were consolidated into this one generic dashboard.
- [x] **Django, PostgreSQL, PgBouncer, and Redis Service Pages**
  - *Details*: All four are served by `generic-service-overview.json` — each is a value of `$service` (`django`, `postgresql`, `pgbouncer`, `redis`, `requests`). The dashboard shows operations with Requests, Error Rate, Throughput, and P50/P95/P99 Latency, plus the Tempo trace list. No per-service dashboard is required.

---

### 4. Endpoint and Operation Instances

- [x] **Instance list for selected endpoint, query, or Redis operation**
  - *Details*: A single details dashboard (`generic-operation-details.json`) with an embedded Tempo trace search panel ("Recent Traces & Flamegraph Waterfall for $operation"), replacing the three former per-component details dashboards.
- [x] **Information to identify and investigate specific request/operation instances**
  - *Details*: Tempo trace list displaying trace IDs, duration, start timestamp, status code, and direct drill-down links ("🔥 Open Flamegraph Waterfall in Tempo").

---

### 5. Request Trace and Waterfall

- [x] **Complete trace and span waterfall for selected request instance**
  - *Details*: Tempo trace waterfall in Grafana rendering parent-child relationships and operation durations.
- [x] **Detailed breakdown of operations in waterfall**:
  - [x] **Django middleware**: `django.middleware.<name>` spans showing exact middleware duration.
  - [x] **View & application functions**: `🐍 django.view.<ViewClass>.<method>` spans.
  - [x] **Template rendering**: `🎨 django.template: <name>` spans.
  - [x] **PostgreSQL queries**: `🐘 SELECT/INSERT` or `🔵` PgBouncer spans with sanitized SQL.
  - [x] **Redis operations**: `🔴 GET/SET/PIPELINE` spans with command parameters.
  - [x] **External API calls**: `🌐 HTTP GET/POST <host>` spans.
  - [x] **Execution duration**: Inclusive/exclusive execution duration recorded for every span.

---

### 6. Service Performance Over Time

- [x] **Time-series graphs for performance over selected time ranges**
  - *Details*: Time-series panels for request volume, P50/P90/P95/P99 latency percentiles, and error rate over time in the generic service dashboard, for any service.
- [x] **Answering "Why is the application slow?" (Root Cause Isolation)**
  - *Details*: Downstream service latency breakdown panels ("% Time Spent by Downstream Service"), throughput comparison across components, and RPS baseline anomaly detection rules.

---

### 7. Time-Period Comparison

- [x] **Compare performance across different time periods** (Today vs. previous day, This week vs. previous week, Before vs. after deployment)
  - *Details*: Prometheus 7-day median rolling baseline queries (`quantile_over_time(0.5, apm_calls_total[7d:1h])`) compared against 5m live rate, deviation percentage calculation, and embedded anomaly detection panels in `tp_trace_service_catalog.json` and `tp_trace_needs_attention.json`. *(Requires ≥7 days of Prometheus history to be meaningful.)*
- [x] **Identify changes in Throughput, Latency, Error Rate, Request Volume**
  - *Details*: Global RPS anomaly list and anomaly threshold alerts (>50% anomaly, >200% severe anomaly).

---

### 8. Sampling and Retention

- [x] **Configurable trace sampling**
  - *Details*: Configurable `sample_rate` parameter in `tp_trace.init()` and `TP_DOG_SAMPLE_RATE` / `OTEL_TRACES_SAMPLER_ARG` env vars, plus per-route overrides via `TP_DOG_ENDPOINT_SAMPLE_RULES` and `TP_DOG_IGNORE_ENDPOINTS`, using the OpenTelemetry `TraceIdRatioBased` sampler under a `ParentBased` wrapper. *(Because RED metrics are derived from the sampled population, sampling policy must account for metric fidelity, not just storage cost.)*
- [x] **Data retention**
  - *Details*: Managed at the backend storage tier — Tempo `block_retention` (currently 336h / 14 days) and the Prometheus TSDB retention flag. *(No Prometheus retention flag is currently set, so it uses the 15-day / 2 GB default.)*

---

### 9. Overall User Flow

- [x] **End-to-End Investigation Flow**: Needs Attention → Service Catalog → Select Service → Select Operation → Instance List → Select Instance → Trace Waterfall → Root Cause.
  - *Details*: Fully wired navigation path across Grafana dashboards:
    1. `tp_trace-needs-attention` (triage) or `tp_trace-project-catalog` (service health)
    2. Click service row → `generic-service-overview?var-service=<service>`
    3. Click operation row → `generic-operation-details?var-service=<service>&var-operation=<operation>`
    4. Click trace instance → Tempo Trace Waterfall in Grafana.
