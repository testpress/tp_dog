# tp_trace — Decision Log

This document records the key technical decisions behind the tp_trace observability stack and the reasoning behind them.

Each decision captures **what was chosen, why it was chosen, and the trade-offs introduced**.

---

## Decision 1 — Use OpenTelemetry as the telemetry foundation

**Why?**

OpenTelemetry provides vendor-neutral APIs, SDKs, telemetry formats, and context propagation. tp_trace uses OTLP for telemetry transport and W3C Trace Context for distributed tracing.

This keeps the instrumentation layer independent from the backend. The same telemetry can be routed to systems such as Tempo, Jaeger, or other OTLP-compatible backends without rewriting application instrumentation.

**Trade-off:**
OpenTelemetry introduces additional infrastructure and requires us to follow its data model and conventions.

---

## Decision 2 — Use custom Django instrumentation

**Why?**

The standard instrumentation provides basic Django tracing, but tp_trace requires deeper application visibility.

Custom instrumentation allows us to capture:

* Normalized HTTP routes
* Middleware execution
* Views and DRF actions
* Template rendering
* Cache operations
* Authentication operations
* Downstream dependency context

**Trade-off:**
We now own part of the Django instrumentation layer and must validate it when upgrading Django.

---

## Decision 3 — Use resolved Django routes instead of raw URLs

**Why?**

A raw URL such as:

```text
/api/courses/12345/
```

can create a separate metric dimension for every course.

Using the resolved route:

```text
/api/courses/{id}/
```

keeps telemetry dimensions bounded and makes endpoint-level metrics more useful.


---

## Decision 4 — Keep route context available during request execution

**Why?**

The route must be available while the request is executing, not only when the request finishes.

This allows downstream spans to inherit:

```text
http.route=/api/courses/{id}/
```

For example:

```text
HTTP Request
 ├── Django View
 ├── PostgreSQL
 └── Redis
```

All can be associated with the same endpoint.

OpenTelemetry's context model is specifically designed to carry execution-scoped information across related operations.

**Trade-off:**
Incorrect context lifecycle handling could result in attributes leaking between requests, so context cleanup must be reliable.

---

## Decision 5 — Build custom PostgreSQL instrumentation

**Why?**

Basic database tracing is not enough here.

We need to understand:

* Which database handled the query
* Primary vs replica
* PgBouncer involvement
* Query execution time
* Sanitized query information

This allows database latency to be investigated in the context of the application's infrastructure topology.

**Trade-off:**
Database-driver behavior becomes part of our instrumentation surface and requires compatibility testing.

---

## Decision 6 — Identify PgBouncer separately from PostgreSQL via Nested Spans

**Why?**

A slow database operation does not necessarily mean PostgreSQL itself is slow. Connection-pool contention (waiting for an available server connection in the pool) occurs before the query reaches PostgreSQL.

By identifying PgBouncer connections separately and structuring pooled queries as nested spans, pooler wait time is visually and temporally distinguishable from PostgreSQL query execution time:

```text
Path A — via Connection Pooler (Nested Two-Span Architecture):
Application ──> PgBouncer :6432 ──> PostgreSQL
    [outer span] 🔹 pgbouncer.pool_wait  (db.system="pgbouncer", db.connection.pool="pgbouncer")
       └── [inner span] 🟢 SELECT ...    (db.system="postgresql", db.statement=..., row_count=...)

Path B — Direct to Database:
Application ─────────────────────> PostgreSQL :5432
    [single span] 🟢 SELECT ...  (db.system="postgresql")
```

**How We Implement It:**
1. **Connection Metadata Inspection**: At cursor execution time, `is_pgbouncer_connection()` inspects Django's active database connection settings:
   - Evaluates whether `db_port == 6432` or the host name matches `pgbouncer`.
2. **Nested Two-Span Model**:
   - **Outer Span** (`🔹 pgbouncer.pool_wait`): Represents the connection-pool phase, attributed with `db.system="pgbouncer"` and `db.connection.pool="pgbouncer"`.
   - **Inner Child Span** (`🟢 SELECT ...`): Wraps the actual query execution with `db.system="postgresql"`, the sanitized SQL statement, and row count metrics.
   - The pool wait overhead corresponds to the duration difference between the outer span and the inner span in the waterfall.
3. **Collector-Side Topology**:
   - The outer span attributes PgBouncer in the service catalog and topology graphs, while the inner span links query execution directly to PostgreSQL.

---

## Decision 7 — Identify primary and replica database roles

**Why?**

Database performance needs to be analyzed by topology.

A slow query against a read replica has a different investigation path from a slow query against the primary database.

tp_trace therefore attaches database-role information to database spans where it can be determined reliably.

**How We Detect It:**
1. **Alias & Map Heuristics**: Role is resolved from the Django database connection alias alone, in this precedence order:
   - An exact match in `db_role_map` (`TP_DOG_DB_ROLE_MAP`).
   - A case-insensitive match in `db_role_map`.
   - A pattern match on the alias (`replica`, `slave`, `readonly`, `read`, optionally suffixed with digits or `db`).
   - Otherwise `primary`.

   Host and port are **not** consulted for role. If your alias naming does not
   signal topology, set `TP_DOG_DB_ROLE_MAP` explicitly.
2. **Explicit Span Attribution**: Emits `db.role` (`primary` or `replica`) on client database spans.
3. **Multi-DB Routing Parity**: Supports Django multi-database routing topologies. A single CLIENT span carries both the connection alias (`peer.service`) and the physical database it resolved to (`db.name`), so routing is attributable without a second parent span.

**Trade-off:**
Role detection depends on deployment configuration and connection naming conventions.

---

## Decision 8 — Sanitize SQL before exporting telemetry

**Why?**

Raw SQL can contain sensitive values and can also create unnecessary query cardinality.

For example:

```sql
SELECT * FROM users WHERE id = 12345
```

can be represented as:

```sql
SELECT * FROM users WHERE id = ?
```

The goal is to preserve the query shape without exporting literal values.

**Trade-off:**
SQL sanitization is not a complete security boundary. It must be tested against different SQL syntax and should not be treated as a guarantee that every sensitive value will always be removed.

---

## Decision 9 — Use lightweight Redis instrumentation

**Why?**

Redis has clear operation boundaries, so it does not require the same depth of customization as Django or PostgreSQL.

The integration focuses on useful metadata such as:

* Redis command
* Pipeline information
* Operation timing
* Sensitive argument handling

**Trade-off:**
Some internal Redis-client behavior may remain invisible.

---

## Decision 10 — Use lightweight outbound HTTP instrumentation

**Why?**

Outbound HTTP calls are important dependency spans, but they do not require a completely custom HTTP client implementation.

The wrapper captures:

* HTTP method
* Destination
* Duration
* Trace context
* Sanitized URL information

W3C Trace Context provides the standard mechanism for propagating tracing information between services.

**Trade-off:**
The instrumentation remains dependent on the behavior of the underlying HTTP client.

---

## Decision 11 — Use lightweight Boto3 instrumentation

**Why?**

AWS operations need to appear as dependency spans, but tp_trace does not require a custom AWS telemetry engine.

The integration captures useful information such as:

* AWS service
* Operation
* Relevant resource information
* Duration

**Trade-off:**
The integration must be tested against the Boto3 APIs and operations we actually use.

---

## Decision 12 — Generate span metrics in the Collector

**Why?**

The application already produces spans containing timing and status information.

Instead of maintaining another metric-calculation system inside every Python process, the Collector can derive request, error, and duration metrics from spans using the `spanmetrics` connector. The connector is specifically designed to aggregate RED metrics from span data.

The architecture is therefore:

```text
Application
     │
     │ Traces
     ▼
OTel Collector
     │
     ├── Tempo
     │
     └── Span Metrics
            │
            ▼
        Prometheus
```

**Trade-off:**
The Collector becomes an important part of the metrics pipeline and must be sized and operated accordingly.

---

## Decision 13 — Keep telemetry failures isolated from the application

**Why?**

Observability should not become a prerequisite for application availability.

If the Collector is unavailable because of:

* Network failure
* DNS failure
* Collector restart
* Backend failure
* Export timeout

the application should continue processing requests.

tp_trace therefore wraps telemetry export with failure handling.

**Trade-off:**
Isolation means telemetry can be lost when the telemetry pipeline is unavailable. Reliability and loss behavior must therefore be measured separately.

---

## Decision 14 — Protect instrumentation from recursive calls

**Why?**

Instrumentation wrappers can call other methods that are themselves instrumented.

Without protection:

```text
wrapper A
  ↓
method A
  ↓
method B
  ↓
wrapper B
  ↓
method A
  ↓
...
```

can produce duplicate spans or recursive instrumentation.

A reentrancy guard ensures that only the intended outer operation creates the span.

**Trade-off:**
The guard must be scoped correctly so legitimate nested operations are not accidentally hidden.

---

## Decision 15 — Use Prometheus exemplars for metric-to-trace navigation

**Why?**

Metrics tell us **that** something is wrong; traces help explain **why**.

Prometheus exemplars provide a link between an aggregated metric measurement and a representative trace. Grafana supports using exemplars to jump from a Prometheus metric directly to a trace in Tempo.

The workflow is:

```text
Metric spike
     ↓
Exemplar
     ↓
Trace ID
     ↓
Tempo
     ↓
Trace waterfall
```

**Trade-off:**
This requires consistent configuration between the metrics system, Grafana, and tracing backend.

---

## Decision 16 — Sanitize telemetry before it leaves the application

**Why?**

Sensitive data must be removed at the point of origin rather than relying on downstream collection layers.

tp_trace applies sanitization before telemetry is exported:

* **SQL Queries**: Replaces numerical literals, string constants, and UUIDs with `%s` parameters to protect customer data and normalize query summaries.
* **URLs**: Strips basic-auth credentials and sensitive URL query tokens.
* **Redis**: Redacts authentication parameters (`AUTH`, `CONFIG`, password arguments).

---

## Decision 17 — Use traces as the primary source for application performance analysis

**Why?**

A distributed trace preserves the exact causal execution tree between an incoming HTTP request and all downstream operations:

```text
HTTP Request
 ├── Django Middleware
 ├── Django View
 ├── PostgreSQL Query (Primary / Replica / PgBouncer)
 ├── Redis Command
 └── External HTTP API
```

Traces serve as the primary diagnostic signal for root-cause analysis (answering *why* a request was slow), while Prometheus provides aggregated time-series metrics (answering *what* the overall error rate and throughput are).

---

## Decision 18 — Run the stack as a production system with explicit operational ownership

**Why?**

tp_trace is the deployed APM system, not a feasibility exercise. That makes its
cost profile and failure modes production concerns rather than validation
outcomes, and it means the following are standing operational responsibilities
rather than pre-adoption checks:

* **Application overhead** — measure SDK CPU/latency overhead per request under
  production concurrency, and re-measure after Django or OpenTelemetry upgrades.
* **Collector sizing** — the Collector sits on the critical path for *both*
  traces and metrics. `memory_limiter` is deliberately configured below the
  container limit, so raising traffic requires re-tuning the limiter, queue
  depth, and `GOMEMLIMIT` together.
* **Sampling policy** — head-based sampling is per-service via
  `TP_DOG_SAMPLE_RATE`, with per-route overrides through
  `TP_DOG_ENDPOINT_SAMPLE_RULES`. Because the Collector derives RED metrics
  from the *sampled* population, dropping the sample rate also drops metric
  fidelity. Errors should be forced to `1.0` rather than sampled down.
* **Storage sizing** — Tempo `block_retention` (currently 14 days) and the
  Prometheus TSDB retention flag are the two knobs that bound disk growth. The
  7-day traffic-anomaly baseline in the Needs Attention dashboard requires at
  least that much Prometheus history to be meaningful.
* **The stack observes itself only partially** — the Collector publishes its own
  telemetry on `:8888`, but Prometheus does not currently scrape it. Adding
  that scrape job is the prerequisite for alerting on queue depth or memory
  pressure.

**Trade-off:**
Committing to a self-hosted stack means the team owns its availability,
upgrades, and capacity. The vendor-neutrality gained in Decision 1 is only
realized if these operational costs are actually paid.

---

## Decision 19 — Treat HTTP 4xx client errors as StatusCode.OK for server error budget

**Why?**

Following Datadog APM conventions, client-induced errors (`400 Bad Request`, `401 Unauthorized`, `403 Forbidden`, `404 Not Found`, `429 Too Many Requests`) are classified as `StatusCode.OK` with `error=False` on the root server span. Only `5xx` server-side errors set `StatusCode.ERROR` and `error=True`.

This ensures that client errors (e.g. bad user credentials or missing resources) do not artificially inflate the service error rate or burn application SLO error budgets in RED metric dashboards.

**Trade-off:**
This diverges from strict OpenTelemetry semantic conventions (which mark 4xx as error when configured). 4xx status codes are still recorded in `http.status_code` and `http.response.status_code` attributes, so they remain fully queryable in TraceQL and status-breakdown panels.

---

# Decision Summary

| Area                      | Decision                             | Primary Benefit |
| :------------------------ | :---------------------------------- | :-------------- |
| **Telemetry Standard**    | OpenTelemetry (OTel)                | Vendor neutrality & open CNCF ecosystem |
| **Transport Protocol**    | OTLP HTTP / gRPC                    | Universal telemetry format |
| **Context Propagation**   | W3C Trace Context (`traceparent`)   | Seamless distributed trace continuation |
| **Django Framework**      | Custom Instrumentation              | Low-cardinality routes, middleware & template waterfalls |
| **Route Normalization**   | Django URL Resolver Matching        | Eliminates Prometheus metric series explosions |
| **Route Attribution**     | In-flight `contextvars`             | Allows downstream DB/Redis metrics to filter by route |
| **PostgreSQL Database**   | Custom Cursor Wrapper               | Primary vs. Replica role tagging & SQL sanitization |
| **PgBouncer Pool**        | Dedicated Topology & `🔵` Icon Tag  | Separates pooled round-trips from direct PostgreSQL |
| **Redis Cache**           | Lightweight Wrapper                 | Command timing, pipeline depth & sensitive arg redaction |
| **Outbound HTTP Calls**   | Lightweight `requests` Wrapper      | Outbound W3C header injection & visual `🌐` naming |
| **AWS SDK (Boto3)**       | Lightweight Wrapper                 | Service, operation, and bucket identification |
| **Metric Generation**     | Server-Side `spanmetrics` Connector | Zero Python CPU/memory overhead for metric math |
| **4xx Error Policy**      | 4xx = OK (5xx = Error)              | Prevents client errors from skewing server SLO error budgets |
| **Failure Isolation**     | `SafeSpanExporter` Wrap             | Telemetry errors NEVER crash or slow down user requests |
| **Reentrancy Safety**     | `reentrant_guard` Context Manager   | Prevents infinite recursion & duplicate span trees |
| **Spike-to-Trace UX**     | Prometheus Exemplars $\rightarrow$ Tempo | Instant jump from metric spike to trace waterfall |
| **Data Privacy**          | Pre-Export Sanitization             | PII & credential scrubbing at the application boundary |
| **Primary Signal**        | Traces (Tempo) + Metrics (Prometheus)| Comprehensive root-cause isolation & high-level health |
| **Operating Model**       | Self-hosted with named ownership    | Vendor neutrality, paid for with explicit ops capacity |


