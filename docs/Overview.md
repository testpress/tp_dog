
# tp_trace Observability Stack

## 1. Overview

**tp_trace** is an OpenTelemetry-based observability stack that **replaces
Datadog APM** with an open, vendor-neutral architecture. It is deployed and in
active use, not a feasibility exercise.

The stack combines:

* **tp_trace SDK** for application instrumentation
* **OpenTelemetry Collector** for telemetry processing and routing
* **Grafana Tempo** for distributed trace storage and TraceQL queries
* **Prometheus** for metrics and time-series analysis
* **Grafana** for dashboards, trace exploration, and incident investigation

The architecture provides the application performance visibility the platform
needs while keeping control over instrumentation, telemetry processing,
dashboards, and storage. The trade-off of that control is that the team owns the
stack's availability, upgrades, and capacity — see
[`Decisions.md`](Decisions.md) (Decision 18).

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

tp_trace provides these capabilities using an OpenTelemetry-based architecture rather than relying on a single proprietary APM vendor.

The workflow it enables is:

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
│        Django App           │
│                              │
│        tp_trace SDK         │
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
| **tp_trace SDK**           | Instruments application operations and generates OpenTelemetry spans         |
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
tp_trace SDK
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

## 5. tp_trace SDK

tp_trace is responsible for application-side instrumentation.

The SDK uses automatic instrumentation so applications can be observed without manually adding tracing code to every operation.

Calling:

```python
tp_trace.init()
```

discovers supported integrations and applies runtime instrumentation.

tp_trace provides instrumentation for:

* Django
* PostgreSQL (`psycopg2`, via Django cursors and the upstream instrumentor)
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

tp_trace derives application RED metrics directly from trace spans using the Collector's `spanmetrics` connector.

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

tp_trace connects the two through Prometheus exemplars.

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

Four pre-provisioned Grafana dashboards are shipped, organized around
operational investigation.

The first two are **generic**: they are driven entirely by the
`normalized.service` and `normalized.operation` attributes the Collector's
`transform/normalize` processor derives from spans, so they work for any
instrumented service without new dashboards. Adding a service to the fleet
automatically adds a row to the Service Catalog.

The dashboard hierarchy moves from high-level health toward detailed diagnosis:

```text
Needs Attention            tp_trace-needs-attention
       │  severity counters + detected-issue cards
       ▼
Service Catalog            tp_trace-project-catalog
       │  auto-detected services, throughput, % time by downstream
       │  (one row per service, each linking to…)
       ▼
Generic Service Overview   generic-service-overview
       │  $service drives the whole dashboard
       │  operations table, each row drilling down to…
       ▼
Generic Operation Details  generic-operation-details
          $operation: hits/errors/status split, latency,
          TraceQL trace list → Tempo waterfall
```

Concretely, the two generic dashboards cover what six component-specific
dashboards used to: a **Django** service, **PostgreSQL**, **PgBouncer**, and
**Redis** are all just values of `$service`, and each endpoint is a value of
`$operation`. There is no per-service dashboard to create or maintain.

The dashboards cover areas including:

* Service health and auto-detection
* Throughput and downstream time attribution
* Error rate
* Latency (P50 / P95 / P99)
* Per-operation drill-down
* Trace investigation and waterfalls
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

These results are workload-specific. They confirm the Collector runs comfortably
inside its configured envelope for this traffic shape, but they are not a
capacity guarantee for other workloads — re-measure before raising traffic.

See [`Test_resource_usage.md`](Test_resource_usage.md).

---

## 14. Key Design Decisions

The stack deliberately makes several architectural decisions.

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

Capturing every trace at high traffic volumes is expensive, so sampling is
configured per service rather than left at 100%.

tp_trace supports sampling strategies such as:

* Head-based sampling
* Parent-based sampling

Sampling is deliberately **head-based only**, applied in the SDK (`tp_trace.sampler`).

The Collector receives the SDK's sampled trace population, derives RED metrics
from it, and writes it to Tempo. Configure the head sample rate per service
with `TP_DOG_SAMPLE_RATE`, and keep specific routes pinned to `1.0` with
`TP_DOG_ENDPOINT_SAMPLE_RULES` (or drop noisy ones with
`TP_DOG_IGNORE_ENDPOINTS`).

Because RED metrics are derived from the **sampled** population, lowering the
sample rate lowers metric fidelity as well as trace volume. Force error routes
to `1.0` rather than sampling them down.

---

## 16. Current Scope

The stack covers:

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

## 17. Current Limitations and Operational Debts

tp_trace is deployed and operational, but it is not feature-complete against a
commercial APM. The following are known and accepted:

### Deliberate scope boundaries

* **WSGI / synchronous Django only.** ASGI, async views, async middleware, and
  streaming responses are not instrumented. The SDK wraps Django's sync
  `BaseHandler.get_response` / `_get_response`; their async counterparts
  (`get_response_async`, `_get_response_async`) are separate methods and are not
  patched. Running under ASGI produces no `django.request` span.
* **No Celery / background-task instrumentation.**
* **Head-based sampling only.** No tail sampling in the Collector.
* **Single-project label model.** `project_name` / `cluster_name` are set per
  process via config or env; there is no multi-tenancy layer in front of Tempo.

### Known gaps

* **The observability stack is only partly self-observed.** The Collector
  publishes its own telemetry on `:8888`, but Prometheus does not scrape it, so
  there is no alerting on Collector queue depth or memory pressure.
* **No Prometheus recording or alerting rules.** Every detection rule is hand-written
  PromQL embedded in a Grafana panel, evaluated at refresh. Nothing pages anyone.
* **The traffic-anomaly baseline needs 7 days of history.** That Needs Attention
  card compares against a `quantile_over_time(...[7d:1h])` baseline, so it cannot
  fire on a freshly provisioned Prometheus.
* **Cardinality safety depends on SQL sanitization.** `db.statement` is a
  `spanmetrics` dimension. Bounded cardinality is a property of
  `sanitize_sql()`, not of the collector configuration.
* **Cached key attributes are exported raw** (`django.cache.key`), which is both a
  PII risk and a cardinality risk for `get_many`/`delete_many`.
* **Function-based middleware is skipped**; only class-based middleware is
  instrumented.
* **PromQL is untested.** The large `label_join`/`label_replace` chains in the
  dashboards are validated by eye, not by CI.

### Standing operational work

Re-measure application overhead after Django or OpenTelemetry upgrades; keep
Collector `memory_limiter` / `GOMEMLIMIT` / queue depth aligned with traffic;
review Tempo `block_retention` and the Prometheus retention flag as data grows;
and maintain dashboards and sampling policy as routes change.

---

## 18. Repository Documentation

The detailed documentation is split by concern:

| Document                                                       | Purpose                                              |
| -------------------------------------------------------------- | ---------------------------------------------------- |
| [`Basics.md`](Basics.md)                                       | Observability and OpenTelemetry fundamentals         |
| [`Architecture.md`](Architecture.md)                           | System architecture and component configuration      |
| [`Components.md`](Components.md)                               | Supported integrations and captured telemetry        |
| [`How_instrumentation_works.md`](How_instrumentation_works.md) | Internal tp_trace instrumentation mechanics         |
| [`Custom_instrumentation.md`](Custom_instrumentation.md)       | Creating new integrations and manual instrumentation |
| [`Dashboards.md`](Dashboards.md)                               | Grafana dashboards and investigation workflow        |
| [`Decisions.md`](Decisions.md)                                 | Architectural decisions and trade-offs               |
| [`Test_resource_usage.md`](Test_resource_usage.md)             | Collector resource and load-test results             |

---

## 19. Summary

The tp_trace stack provides an end-to-end observability architecture based on OpenTelemetry:

```text
┌────────────────────────────────────────────────────────────┐
│                    tp_trace Observability                  │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ Application                                                │
│    │                                                       │
│    ▼                                                       │
│ tp_trace SDK                                              │
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

tp_trace provides the core building blocks of an OpenTelemetry-based APM
platform: instrumented Django request waterfalls, collector-derived RED
metrics, and a metrics-to-trace investigation path. The remaining work is
operational — capacity, retention, self-monitoring, and alerting — rather than
architectural. Known limitations are listed in
[Current Limitations and Operational Debts](#17-current-limitations-and-operational-debts).
