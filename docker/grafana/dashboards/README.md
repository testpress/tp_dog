# TP Trace — Grafana Dashboards

These dashboards provide application performance monitoring (APM) for services instrumented using OpenTelemetry. For implementation details, see the [tp_trace repository](https://github.com/testpress/tp_trace).

## Dashboards

### 1. TP Trace — APM Overview

The home dashboard provides a high-level view of application health and performance across the installed services.

- **Anomalies:** List of detected anomalies, when available.
- **Service metrics:** Requests per second (RPS), error rate (%), and p95 latency.
- **Filters:** Filter metrics by the available time range and dimensions. Testpress also supports filtering by subdomain.
- **Performance trends:** Overall RPS and latency graphs.
- **Service health:** Current health status and the list of monitored services.

### 2. Service Performance — Latency, Throughput & Errors

Each service has an overview dashboard showing:

- RPS, error count, p95 latency, and p99 latency.
- A list of operations within the service, including their RPS, error count, p95 latency, and p99 latency.

### 3. Request & Database Operation Traces — Flamegraphs

Each operation has a detail dashboard. An operation can represent an HTTP endpoint or a SQL query.

The dashboard displays the operation's RPS, error count, and latency metrics, followed by the associated traces for investigation.

### 4. Application Errors — Error Rates & Failure Analysis

Incident triage dashboard detailing failed requests, exception breakdowns by HTTP status code, and live TraceQL error trace waterfalls.

### 5. Application Anomalies & Incidents — Investigation

Automated anomaly and incident triage board surfacing critical and warning level deviations across services and operations.

### 6. OpenTelemetry Collector — Health, Resource Usage & Metrics

The collector dashboard provides visibility into OpenTelemetry Collector fleet health and resource usage, including CPU and memory utilization.

## Service Health Status

The **TP Trace — APM Overview** dashboard assigns health statuses evaluated over a **5-minute rolling window** based on error percentage:

| Status | Error percentage (5m) |
|---|---:|
| **Healthy** (Green) | Below 1% |
| **Warning** (Orange) | 1% to below 5% |
| **Critical** (Red) | 5% and above |
| **No Data** (Gray) | No data available |

## Error Tracking

HTTP 4xx responses are excluded from the APM error count because they generally represent client-side errors. The APM focuses on application and server-side failures.