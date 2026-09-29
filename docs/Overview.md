
# TraceNest Observability PoC

## 1. Overview

**TraceNest** is an OpenTelemetry-based observability stack built as a **proof of concept for replacing Datadog APM capabilities** with an open, vendor-neutral architecture.

The PoC combines:

* **TraceNest SDK** for application instrumentation
* **OpenTelemetry Collector** for telemetry processing and routing
* **Grafana Tempo** for distributed trace storage and TraceQL queries
* **Prometheus** for metrics and time-series analysis
* **Grafana** for dashboards, trace exploration, and incident investigation

The primary objective of the PoC is to validate whether this architecture can provide the application performance visibility required for the Testpress environment while maintaining control over instrumentation, telemetry processing, dashboards, and storage.

---

## 2. What Problem Does This Solve?

Traditional APM platforms provide a single integrated experience for:

* Request tracing
* Endpoint performance
* Error monitoring
* Database performance
* Cache performance
* Dependency analysis
* Dashboards
* Root-cause investigation

The PoC reproduces these capabilities using an OpenTelemetry-based architecture rather than relying on a single proprietary APM vendor.

The intended workflow is:

```text
                         Application Issue
                                │
                                ▼
                       Grafana Dashboard
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
             RPS            Error Rate          Latency
              │                 │                 │
              └─────────────────┼─────────────────┘
                                ▼
                         Trace / Exemplar
                                │
                                ▼
                         Tempo Waterfall
                                │
              ┌─────────────────┼──────────────────┐
              ▼                 ▼                  ▼
           Django            PostgreSQL           Redis
              │                 │                  │
              └─────────────────┼──────────────────┘
                                ▼
                         Root Cause Analysis
```

The goal is to move from **"something is slow"** to **"this specific operation caused the slowdown"** using a single investigation workflow.

---

## 3. High-Level Architecture

```text
┌──────────────────────────────┐
│        Testpress App         │
│                              │
│        TraceNest SDK         │
│                              │
│ Django / PostgreSQL / Redis  │
│ HTTP / Boto3 / custom spans  │
└──────────────┬───────────────┘
               │
               │ OTLP HTTP / gRPC
               ▼
┌─────────────────────────────────────────┐
│          OpenTelemetry Collector        │
│                                         │
│  Receivers                              │
│     ├── OTLP HTTP :4318                │
│     ├── OTLP gRPC :4317                │
│     └── PostgreSQL metrics             │
│                                         │
│  Processing                             │
│     ├── Batch                           │
│     └── Spanmetrics                     │
│                                         │
│  Export                                 │
│     ├── Traces ──────────────► Tempo   │
│     └── Metrics ────────────► Prometheus│
└────────────────┬──────────────┬─────────┘
                 │              │
                 ▼              ▼
        ┌──────────────┐  ┌──────────────┐
        │ Grafana Tempo│  │  Prometheus  │
        │              │  │              │
        │ Trace Store  │  │ Time Series  │
        │ TraceQL      │  │ PromQL       │
        └──────┬───────┘  └──────┬───────┘
               │                  │
               └────────┬─────────┘
                        ▼
                 ┌──────────────┐
                 │   Grafana    │
                 │              │
                 │ Dashboards   │
                 │ Trace UI     │
                 │ Flamegraphs  │
                 └──────────────┘
```

Detailed architecture is documented in [`Architecture.md`](Architecture.md).

---

## 4. Core Components

| Component                   | Responsibility                                                               |
| --------------------------- | ---------------------------------------------------------------------------- |
| **TraceNest SDK**           | Instruments application operations and generates OpenTelemetry spans         |
| **OpenTelemetry Collector** | Receives, batches, processes, and routes telemetry                           |
| **Spanmetrics**             | Derives RED metrics from trace spans                                         |
| **Grafana Tempo**           | Stores and queries distributed traces                                        |
| **Prometheus**              | Stores time-series metrics and supports PromQL analysis                      |
| **Grafana**                 | Provides dashboards, metric visualization, trace search, and waterfall views |

### Data flow

```text
Application
    │
    │ Spans
    ▼
TraceNest SDK
    │
    │ OTLP
    ▼
OTel Collector
    │
    ├──────────────► Tempo
    │                  │
    │                  └── TraceQL / Waterfall
    │
    └──────────────► Spanmetrics
                       │
                       ▼
                   Prometheus
                       │
                       └── PromQL / Dashboards

Tempo + Prometheus
        │
        ▼
     Grafana
```

---

## 5. TraceNest SDK

TraceNest is responsible for application-side instrumentation.

The SDK uses automatic instrumentation so applications can be observed without manually adding tracing code to every operation.

Calling:

```python
tracenest.init()
```

discovers supported integrations and applies runtime instrumentation.

The current PoC includes instrumentation for:

* Django
* PostgreSQL (`psycopg2` / `psycopg3`)
* PgBouncer
* Redis
* `django_redis`
* Outbound HTTP requests using `requests`
* AWS operations using Boto3
* Custom application operations

The SDK also provides primitives for adding custom instrumentation when automatic instrumentation is insufficient.

See:

* [`Components.md`](Components.md)
* [`How_instrumentation_works.md`](How_instrumentation_works.md)
* [`Custom_instrumentation.md`](Custom_instrumentation.md)

---

## 6. What Gets Captured?

A typical request produces a trace similar to:

```text
django.request
│
├── Django Middleware
│
├── Django View
│   │
│   ├── PostgreSQL Query
│   │
│   ├── Redis Command
│   │
│   └── External HTTP Request
│
└── Template Rendering
```

Each operation contains information such as:

* Duration
* Parent/child relationship
* Operation name
* Status
* HTTP information
* Database information
* Redis information
* Exception information
* Trace and span identifiers

This produces a complete execution waterfall for an HTTP request.

---

## 7. Database and Dependency Visibility

### PostgreSQL

The PostgreSQL instrumentation captures:

* Query duration
* Sanitized SQL statements
* Database operation
* Database instance
* Primary vs replica routing
* PgBouncer usage

PgBouncer is represented separately so that connection-pool related behavior can be distinguished from actual PostgreSQL query execution.

### Redis

Redis instrumentation captures:

* Command
* Duration
* Database operation
* Pipeline execution
* Django cache operations
* Cache hit information where available

Sensitive Redis command arguments are redacted.

### HTTP Dependencies

Outbound HTTP requests capture:

* HTTP method
* Destination
* Duration
* Status code
* Trace context

W3C Trace Context propagation allows downstream services to continue the same distributed trace.

### AWS / Boto3

AWS operations can be represented as dependency spans containing service, operation, and relevant resource information.

---

## 8. Metrics

The PoC derives application RED metrics directly from trace spans using the Collector's `spanmetrics` connector.

The primary metrics include:

### Rate

Request volume / throughput.

```promql
sum(rate(apm_calls_total[5m])) by (http_route)
```

### Errors

Server-side error rate based on the configured error policy.

### Duration

Latency distributions represented as Prometheus histograms.

This allows dashboards to calculate:

* P50
* P90
* P95
* P99

using `histogram_quantile()`.

The key benefit is that the application does not need a separate metrics implementation for these application performance signals.

---

## 9. Trace-to-Metric Investigation

Metrics answer:

> **What is happening?**

Traces answer:

> **Why is it happening?**

The PoC connects the two through Prometheus exemplars.

The intended investigation path is:

```text
Metric anomaly
      │
      ▼
Prometheus / Grafana
      │
      ▼
Exemplar
      │
      ▼
Trace ID
      │
      ▼
Tempo
      │
      ▼
Waterfall
      │
      ▼
Slow operation / failing dependency
```

This allows an engineer to move from an aggregate metric such as latency or error rate directly into a representative trace.

---

## 10. Grafana Dashboards

The PoC contains a set of pre-provisioned Grafana dashboards organized around operational investigation.

The dashboard hierarchy moves from high-level health toward detailed diagnosis:

```text
Needs Attention
       │
       ▼
Service Catalog
       │
       ├── Django
       │     └── Endpoint Details
       │
       ├── PostgreSQL
       │     └── Query Details
       │
       └── Redis
             └── Command Details
```

The dashboards cover areas including:

* Service health
* Throughput
* Error rate
* Latency
* Slow endpoints
* Database performance
* Redis performance
* Trace investigation
* Resource usage
* Traffic anomalies

Detailed dashboard behavior and navigation is documented in [`Dashboards.md`](Dashboards.md).

---

## 11. Security and Data Sanitization

Telemetry is sanitized before it leaves the application.

Examples include:

### SQL

Literal values, IDs, strings, and other sensitive values are normalized.

```text
SELECT * FROM users WHERE email = 'bob@example.com'
```

becomes conceptually:

```text
SELECT * FROM users WHERE email = ?
```

### URLs

Basic-auth credentials and sensitive query parameters are removed.

### Redis

Sensitive authentication-related command arguments are redacted.

The objective is to prevent sensitive application data from unnecessarily entering the telemetry pipeline.

---

## 12. Reliability Model

Observability must not become a dependency of application availability.

The SDK therefore isolates telemetry failures from the application.

For example:

```text
Application
    │
    ├── Business request ───────────────► User
    │
    └── Telemetry export
             │
             ├── Collector available
             │       └── Telemetry exported
             │
             └── Collector unavailable
                     └── Telemetry dropped safely
```

`SafeSpanExporter` catches telemetry export failures so that Collector or backend failures do not crash the application request path.

The trade-off is that telemetry can be lost while the observability pipeline is unavailable.

---

## 13. Resource Usage Validation

The Collector was tested under continuous application load with the following configured limits:

| Resource |      Limit |    Observed |
| -------- | ---------: | ----------: |
| CPU      | `0.5` vCPU |     ~40–50% |
| Memory   |   `256 MB` | ~217–225 MB |
| Network  |          — |   ~469 kB/s |
| Disk     |          — |   ~914 kB/s |

During the tested workload:

* CPU remained within the configured limit.
* Memory remained below the `256 MB` limit.
* No OOM condition was observed.
* The Collector continued processing trace and metric traffic.

These results are workload-specific and should be treated as PoC validation rather than a production capacity guarantee.

See [`Test_resource_usage.md`](Test_resource_usage.md).

---

## 14. Key Design Decisions

The PoC deliberately makes several architectural decisions.

| Area                      | Decision                          |
| ------------------------- | --------------------------------- |
| Telemetry standard        | OpenTelemetry                     |
| Transport                 | OTLP HTTP / gRPC                  |
| Distributed context       | W3C Trace Context                 |
| Trace backend             | Grafana Tempo                     |
| Metrics backend           | Prometheus                        |
| Visualization             | Grafana                           |
| Metric generation         | Collector-side `spanmetrics`      |
| Django instrumentation    | Custom instrumentation            |
| PostgreSQL                | Custom cursor instrumentation     |
| PgBouncer                 | Explicit topology identification  |
| Redis                     | Lightweight instrumentation       |
| HTTP                      | `requests` instrumentation        |
| AWS                       | Lightweight Boto3 instrumentation |
| Metric → trace navigation | Prometheus exemplars              |
| Data protection           | Pre-export sanitization           |
| Application isolation     | Safe telemetry export             |
| Recursive instrumentation | Reentrancy guard                  |

The reasoning behind these decisions and their trade-offs is documented in [`Decisions.md`](Decisions.md).

---

## 15. Sampling

Sampling is an important part of the eventual production architecture because capturing every trace at high traffic volumes can become expensive.

The PoC supports sampling strategies such as:

* Head-based sampling
* Parent-based sampling

Sampling is deliberately **head-based only**, applied in the SDK (`tracenest.sampler`).

The Collector receives the SDK's sampled trace population, derives RED metrics
from it, and writes it to Tempo. Configure the head sample rate per service
with `TRACENEST_SAMPLE_RATE` when storage capacity requires it.

---

## 16. Current PoC Scope

The current PoC demonstrates:

* Application distributed tracing
* Django request waterfalls
* PostgreSQL tracing
* PgBouncer visibility
* Redis tracing
* Outbound HTTP tracing
* Boto3 tracing
* RED metrics
* PromQL-based analysis
* TraceQL-based trace search
* Grafana APM dashboards
* Metric-to-trace navigation
* Data sanitization
* Telemetry failure isolation
* Collector resource testing

---

## 17. PoC Limitations

This is a **technical feasibility and validation PoC**, not a production-ready replacement architecture.

Before production adoption, additional validation is required around:

* Application CPU and latency overhead under production concurrency
* Collector behavior during sustained traffic bursts
* Collector memory and queue sizing
* Telemetry loss during backend or network failures
* Production sampling strategy
* Trace storage sizing and retention
* Multi-tenant storage architecture
* Operational monitoring of the observability stack itself
* Long-term dashboard and alert maintenance

These areas should be validated using representative production workloads rather than relying only on the current PoC test.

---

## 18. Repository Documentation

The detailed documentation is split by concern:

| Document                                                       | Purpose                                              |
| -------------------------------------------------------------- | ---------------------------------------------------- |
| [`Basics.md`](Basics.md)                                       | Observability and OpenTelemetry fundamentals         |
| [`Architecture.md`](Architecture.md)                           | System architecture and component configuration      |
| [`Components.md`](Components.md)                               | Supported integrations and captured telemetry        |
| [`How_instrumentation_works.md`](How_instrumentation_works.md) | Internal TraceNest instrumentation mechanics         |
| [`Custom_instrumentation.md`](Custom_instrumentation.md)       | Creating new integrations and manual instrumentation |
| [`Dashboards.md`](Dashboards.md)                               | Grafana dashboards and investigation workflow        |
| [`Decisions.md`](Decisions.md)                                 | Architectural decisions and trade-offs               |
| [`Test_resource_usage.md`](Test_resource_usage.md)             | Collector resource and load-test results             |

---

## 19. Summary

The TraceNest PoC demonstrates an end-to-end observability architecture based on OpenTelemetry:

```text
┌────────────────────────────────────────────────────────────┐
│                        TraceNest PoC                       │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ Application                                                │
│    │                                                       │
│    ▼                                                       │
│ TraceNest SDK                                              │
│    │                                                       │
│    ▼                                                       │
│ OpenTelemetry Collector                                    │
│    │                                                       │
│    ├──────────────► Tempo ───────────► Traces              │
│    │                                                       │
│    └──────────────► Prometheus ───────► Metrics            │
│                         │                                  │
│                         └──────────────► Grafana           │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

The architecture provides a unified workflow:

**Detect → Investigate → Correlate → Identify Root Cause**

Metrics provide the high-level operational view, while distributed traces provide the detailed execution path needed for root-cause analysis.

The PoC therefore validates the core technical building blocks required for an OpenTelemetry-based APM platform while clearly separating **demonstrated capabilities** from **production-scale validation that remains to be completed**.
