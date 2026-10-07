# Technical Implementation Plan: TraceNest SDK Instrumentation Remediation

This plan details the step-by-step remediation of all bugs, anti-patterns, span amplifications, and OpenTelemetry semantic convention violations identified during the deep review of TraceNest SDK instrumentations.

---

## 1. Objectives & Guiding Principles

1. **Zero Swallowed Exceptions & No Broken Wrappers**:
   - Eliminate redundant monkey-patching in the Redis integration that causes swallowed `TypeError` exceptions.
   - Rely natively on OpenTelemetry's established suppression mechanisms (`suppress_instrumentation()`).
2. **Eliminate Span Amplification & Waterfall Bloat**:
   - Reduce the 4–5 nested view spans per request down to a clean, single `INTERNAL` view span.
   - Retain PgBouncer as a dedicated connection pooler span in the trace waterfall and dashboard metrics, while ensuring clean query attributes and synchronization.
3. **OpenTelemetry Semantic Convention Compliance**:
   - Stop injecting server `http.route` and `http.method` into downstream child spans (Postgres, Redis, Boto3, outgoing HTTP client calls).
   - Stop setting `http.route = str(status_code)` (e.g. `"404"`, `"500"`) on unmatched requests.
   - Stop forcing `StatusCode.OK` on normal successful spans or 4xx client responses; leave successful spans as `StatusCode.UNSET`.
4. **Fix Dead Code & Processors**:
   - Remove no-op `set_attribute` calls in `RouteEnrichingSpanProcessor.on_end()` after span termination.
5. **Enrich Missing Metadata**:
   - Extract S3 bucket and DynamoDB table names from Boto `params`.
   - Extract Redis connection kwargs (host, port, db index) into span attributes.

---

## 2. Phase-by-Phase Remediation Breakdown

### Phase 1: Core Engine & Processors (`route_context.py` & `tracing.py`)

#### Problem:
- `RouteEnrichingSpanProcessor.on_start()` copies `http.method` onto ANY child span started during a request (causing DB queries and Redis commands to have `http.method = "GET"`).
- `RouteEnrichingSpanProcessor.on_end()` calls `span.set_attribute()` after the span has ended, which is a complete no-op in the OTel SDK.
- `traced_span()` forces `StatusCode.OK` on all spans that don't raise an exception.

#### Changes:
1. In `src/tracenest/route_context.py`:
   - In `on_start()`: Only stamp `http.route` on spans where `span.kind == SpanKind.SERVER` or spans that explicitly represent HTTP endpoints. Do NOT stamp `http.method` or `http.request.method` onto non-HTTP client/internal spans.
   - In `on_end()`: Remove dead `span.set_attribute()` calls. Any required normalization must be computed during span start or directly at the call site within the integration.
2. In `src/tracenest/tracing.py`:
   - In `traced_span()`: Remove `span.set_status(StatusCode.OK)`. Leave status as `StatusCode.UNSET` on success, reserving `StatusCode.ERROR` strictly for caught exceptions.

---

### Phase 2: Redis Integration Cleanup (`redis/integration.py`)

#### Problem:
- `_redis_suppress_guard` strips `__wrapped__` down to the raw unbound function and calls `orig(*args, **kwargs)` without `instance`, throwing a `TypeError` that is caught and swallowed on every cache operation.
- Wraps 11 separate Redis methods redundantly, including factory methods like `pipeline()`.
- Lacks connection attributes (`server.address`, `server.port`).

#### Changes:
1. In `src/tracenest/integrations/redis/integration.py`:
   - Delete `_redis_suppress_guard`.
   - In `_apply_patch()`: Remove the loop wrapping the 11 targets (`execute_command`, `pipeline`, etc.).
   - Rely solely on `RedisInstrumentor().instrument(request_hook=..., response_hook=...)`. OpenTelemetry's `RedisInstrumentor` already internally checks `is_instrumentation_enabled()`, which is disabled by `django/cache.py` via `suppress_instrumentation()`.
   - In `_tracenest_redis_request_hook`:
     - Inspect `instance.connection_pool.connection_kwargs` (or `instance._kwargs`) to populate `server.address`, `server.port`, and `db.redis.database_index` when available.
2. In `tests/test_redis.py`:
   - Update tests to verify that `RedisInstrumentor` cleanly suppresses driver spans when `suppress_instrumentation()` is held, without throwing or catching any exceptions.

---

### Phase 3: PostgreSQL & PgBouncer Visibility & Optimization (`postgres/cursor.py` & `integration.py`)

#### Requirement:
- **Retain PgBouncer as a Distinct Visible Component**: PgBouncer must remain visible in both the Grafana Tempo trace waterfall (showing the request traversing the connection pooler) and in Prometheus / Grafana dashboards (`$service=pgbouncer` in Service Catalog and Service Overview).

#### Architecture & Design:
1. **Preserve PgBouncer in Trace Waterfall**:
   - Keep the PgBouncer span (`🔹 pgbouncer.pool_wait`) as the parent CLIENT span wrapping the PostgreSQL query execution when connecting via PgBouncer (host `pgbouncer` or port `6432`).
   - This ensures the trace flamegraph clearly displays the full request path:
     `Django View` ➔ `🔹 pgbouncer.pool_wait [pgbouncer]` ➔ `🟢 SQL Query [postgresql]`.
2. **Preserve Service Catalog & Golden Signal Metrics**:
   - Ensure the outer span sets:
     - `db.system = "pgbouncer"`
     - `normalized.service = "pgbouncer"`
     - `normalized.operation = "pgbouncer.pool_wait"`
     - `server.address = "pgbouncer"` (or host), `server.port = 6432`
   - This guarantees that Prometheus `apm_calls_total` and `apm_duration_milliseconds_bucket` continue to power the **PgBouncer** row in the Service Catalog and the `$service=pgbouncer` Golden Signals overview.
3. **Refine Attributes & Guard Hygiene**:
   - Ensure inner Postgres query span has `db.system = "postgresql"` and `normalized.service = "postgres"`.
   - Set full sanitized SQL in `db.statement` / `db.query.text` on the inner SQL span.
   - Synchronize re-entrancy checks so neither `conn.execute_wrappers` nor `CursorWrapper.execute` causes redundant query execution or duplicate inner spans.

---

### Phase 4: Requests (HTTP Client) Integration (`requests/client.py`)

#### Problem:
- Lines 84–86 inject the in-flight Django server `http.route` into outgoing HTTP client spans (`span.set_attribute("http.route", route)`), causing spanmetrics label pollution in Prometheus.

#### Changes:
1. In `src/tracenest/integrations/requests/client.py`:
   - Remove `span.set_attribute("http.route", route)`.
   - If endpoint correlation is desired, record `tracenest.originating_route = route` or rely on W3C distributed trace parentage.
2. In `tests/test_requests.py`:
   - Verify client spans do not carry server `http.route`.

---

### Phase 5: Django Integration Optimization (`view.py`, `request.py`, `middleware.py`, `middleware_init.py`)

#### Problem:
- **View Span Amplification**: Emits up to 4 nested view spans (`BaseHandler._get_response`, `View.dispatch`, `View.get`, `View.setup`) per request.
- **Runtime Mutation**: Mutates `instance` attributes on the fly during `dispatch()`.
- **404 Route Pollution**: Sets `http.route = "404"` and forces `StatusCode.OK` on 4xx responses.
- **Middleware Nesting**: Tracing `__call__` on `MiddlewareMixin` leaves all middleware spans open until the view finishes.

#### Changes:
1. In `src/tracenest/integrations/django/view.py`:
   - Consolidate view tracing: Retain the primary `INTERNAL` view span emitted by `BaseHandler._get_response` (`django.view.<view_name>`).
   - Remove `View.setup` and `_traced_handler` wrapping on individual HTTP action methods (`get`, `post`).
   - Simplify `View.dispatch` so it only records DRF action metadata onto the active view span if not already present, without mutating instance attributes via `setattr`.
2. In `src/tracenest/integrations/django/request.py`:
   - If a route is unmatched (e.g. 404 or unrouted):
     - Do NOT set `http.route`.
     - Keep span name as `{method}` or `{method} /404` without injecting `"404"` into the `http.route` label.
   - For HTTP 4xx responses (400, 401, 403, 404, 429):
     - Do NOT mark `StatusCode.OK`; leave status as `StatusCode.UNSET`.
     - Do NOT set `error = False`.
3. In `src/tracenest/integrations/django/middleware.py`:
   - Ensure middleware spans measure only their own hook duration (`process_request`, `process_response`) rather than wrapping the entire downstream chain inside `__call__`.
4. In `src/tracenest/integrations/django/middleware_init.py`:
   - Add a fallback in `TraceNestMiddleware.__init__`: If `BaseHandler.load_middleware` was already executed, trigger an immediate sweep of `settings.MIDDLEWARE` to wrap registered middleware classes.

---

### Phase 6: Boto3 Enrichment (`boto/integration.py`)

#### Problem:
- Ignores `params`, missing critical S3 bucket name and DynamoDB table name context.

#### Changes:
1. In `src/tracenest/integrations/boto/integration.py`:
   - In `_tracenest_boto_request_hook`:
     - If `params` is a dict:
       - Extract `Bucket` -> `aws.s3.bucket`
       - Extract `Key` -> `aws.s3.key` (sanitized)
       - Extract `TableName` -> `aws.dynamodb.table_name`
     - Attach these attributes to the span if present.

---

## 3. Verification & Testing Strategy

1. **Unit Test Suite**:
   - Run existing unit test suites for all modified integrations (`test_django.py`, `test_django_advanced.py`, `test_postgres.py`, `test_redis.py`, `test_requests.py`, `test_boto.py`).
   - Ensure no regressions occur while adjusting assertions to match the streamlined span structure.
2. **Span Count & Trace Waterfall Verification**:
   - Verify that a standard Django request produces a clean, readable waterfall:
     ```text
     SERVER: GET /api/products/ [django]
       ├── INTERNAL: django.middleware.SecurityMiddleware [django]
       ├── INTERNAL: django.view.ProductView.get [django]
       │     ├── CLIENT: SELECT api_product ... [postgres]
       │     ├── CLIENT: GET product:123 [redis]
       │     └── CLIENT: GET https://api.stripe.com [requests]
     ```
   - Confirm that redundant `dispatch`, `setup`, and synthetic `pgbouncer.pool_wait` spans are gone.
3. **Cardinality & Label Audit**:
   - Ensure no `http.route` or `http.method` tags leak onto Postgres, Redis, or Requests spans.
   - Ensure `http.route="404"` is eliminated from Prometheus metric series.
