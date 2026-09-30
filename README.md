# TraceNest

TraceNest is an OpenTelemetry-based observability SDK for synchronous Django applications. It creates a Django request waterfall without application-code changes, then exports standard OTLP traces to an OpenTelemetry Collector (which automatically derives RED metrics in real time).

## Documentation

For a comprehensive overview of the architecture, components, workflows, and decision log, see:

- [**TraceNest Observability Overview**](docs/Overview.md) — Complete end-to-end technical overview, architecture, component breakdown, resource usage benchmarks, current limitations, and decision index.
- [**Operations Runbook**](docs/Runbook.md) — On-call operational guide, 6 essential questions with single-line answers, emergency killswitches, and collector diagnostics.

---

The SDK is backend-neutral: use any OTLP-compatible collector and a trace backend such as Tempo to inspect individual request waterfalls.

## What Django instrumentation captures

One request produces a single trace with this hierarchy:

```text
django.request [SERVER]
└── django.middleware.SecurityMiddleware
    └── ... other configured middleware
        └── django.view.ProductTemplateView
            └── django.template: products/list.html
                └── included templates
```

The root request span includes the HTTP method, sanitized URL, low-cardinality route, status code, trace context, and errors. The SDK also records request count, error count, and request-duration metrics.

## Requirements and scope

- Python 3.8.18+
- Django 3.2+
- Synchronous Django / WSGI applications
- An OTLP HTTP collector endpoint (default: `http://localhost:4318`)

ASGI, async views, async middleware, and streaming-response instrumentation are not supported in this version. Function-based middleware is skipped; class-based middleware is instrumented.

## Install

From this repository:

```bash
python -m pip install -e .
```

For development and tests:

```bash
python -m pip install -e '.[dev]'
```

## Add the SDK to a Django app

### Option 1: Direct initialization in settings.py (recommended)

A single `init()` call handles everything — it auto-detects installed integrations (Django, PostgreSQL, Redis, HTTP client) and patches them automatically:

```python
# settings.py
import tracenest

tracenest.init(
    project_name="my-django-app",
    environment="production",
    endpoint="http://otel-collector:4318",
)
```

That's it. No `patch_all()` needed — `auto_patch=True` is the default.

### Option 2: Django middleware

Add the TraceNest middleware to your `MIDDLEWARE` list. It initializes the SDK on the first request:

```python
MIDDLEWARE = [
    "tracenest.integrations.django.TraceNestMiddleware",
    # ... your other middleware ...
]
```

Configuration comes from environment variables (`TRACENEST_PROJECT_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, etc.).

### Option 3: Environment variables only

The most minimal setup — just set environment variables and call `init()` with no arguments:

```python
import tracenest

tracenest.init()
```

### Option 4: Custom bootstrap function

For more control (error handling, conditional setup), create a bootstrap function:

```python
# config/otel.py
import os
import logging

logger = logging.getLogger("config.otel")


def setup_telemetry():
    if os.environ.get("TRACENEST_DISABLED", "").lower() in ("1", "true"):
        return
    try:
        import tracenest
    except ImportError:
        return
    try:
        tracenest.init(
            project_name=os.environ.get("TRACENEST_PROJECT_NAME", "my-app"),
            environment=os.environ.get("TRACENEST_ENVIRONMENT", "development"),
            endpoint=os.environ.get("TRACENEST_ENDPOINT", "http://otel-collector:4318"),
        )
        logger.info("TraceNest initialized")
    except Exception:
        logger.exception("TraceNest init failed")
```

Call `setup_telemetry()` from your `settings.py`.

## Configuration

Explicit `init()` arguments take precedence over environment variables, which take precedence over defaults.

| Setting | Environment variable | Default |
| --- | --- | --- |
| Project name | `TRACENEST_PROJECT_NAME` or `OTEL_SERVICE_NAME` | `unknown-project` |
| Environment | `TRACENEST_ENVIRONMENT` or `OTEL_ENVIRONMENT` | `development` |
| Version | `TRACENEST_VERSION` or `OTEL_SERVICE_VERSION` | `0.1.0` |
| OTLP base endpoint | `TRACENEST_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` |
| Trace endpoint | `TRACENEST_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Derived as `<endpoint>/v1/traces` |
| Metric endpoint | `TRACENEST_METRICS_ENDPOINT` or `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | Derived as `<endpoint>/v1/metrics` |
| Trace sample rate | `TRACENEST_SAMPLE_RATE` or `OTEL_TRACES_SAMPLER_ARG` | `1.0` |
| Disable the SDK | `TRACENEST_DISABLED` or `OTEL_SDK_DISABLED` | `false` |
| Debug logging | `TRACENEST_DEBUG` or `OTEL_LOG_LEVEL=debug` | `false` |
| Auto-patch integrations | `auto_patch` kwarg | `true` |

When `auto_patch=True` (the default), all installed integrations are automatically detected and patched during `init()`. This includes Django, PostgreSQL, Redis, and HTTP client instrumentors. You can disable specific integrations via the `integrations` dict:

```python
tracenest.init(
    integrations={"redis": False, "requests": False},  # disable Redis and HTTP client tracing
)
```

Useful Django template options:

```python
tracenest.init(
    trace_nested_templates=True,  # default True: captures template includes
    template_enabled=True,
    template_exclude=["django/forms/*", "debug_toolbar/*", "*/widgets/*"],
)
```

## Trace and metric data

The SDK emits standard OpenTelemetry spans. The OpenTelemetry Collector's `spanmetrics` connector then automatically derives RED metrics without client-side metric calculation overhead:

| Telemetry | Name | Generated By | Purpose |
| --- | --- | --- | --- |
| Trace | `django.request` | TraceNest SDK | Root `SERVER` span for each HTTP request |
| Trace | `django.middleware.<name>` | TraceNest SDK | Class-based middleware execution |
| Trace | `django.view.<name>` | TraceNest SDK | Django/DRF view execution |
| Trace | `django.template: <name>` | TraceNest SDK | Template and included-template rendering |
| Metric | `apm_calls_total` | Collector (`spanmetrics`) | Request & error counter partitioned by route and status |
| Metric | `apm_duration_milliseconds_bucket` | Collector (`spanmetrics`) | Latency histogram buckets for P50/P90/P95/P99 duration |

The SDK extracts an incoming W3C `traceparent` header, so a Django request continues an existing distributed trace. Traced responses include `X-Trace-ID` and `X-Span-ID` headers for correlation.

## Run the included demo

The repository includes a Django app and a local OpenTelemetry Collector, Tempo, Prometheus, and Grafana stack.

```bash
docker compose up --build
curl http://localhost:8001/api/products-tmpl/
```

Open Grafana at [http://localhost:3000](http://localhost:3000), then open the Tempo datasource or the Django dashboard. Search Tempo with:

```traceql
{ span.http.route = "/api/products-tmpl/" }
```

Select a `django.request` trace to open its full waterfall. Middleware timings are inclusive because Django middleware is nested: do not add their durations together.

### Load test S3 and confirm the boto spans land

`loadtest_s3.py` drives the sample app's S3 endpoint under controlled concurrency, reports per-operation latency, and then verifies against Tempo that the matching boto spans were actually recorded.

```bash
./loadtest_s3.py                          # 10s, 4 workers, put/get/delete
./loadtest_s3.py -d 60 -c 16 -r 50        # 60s, 16 workers, capped at 50 req/s
./loadtest_s3.py -n 200 --ops put,get     # exactly 200 requests
./loadtest_s3.py --require-traces         # exit 2 if no S3 spans reach Tempo
```

Operations are selected with `--ops` from `put`, `get`, `delete`, `list`, `head`; the default `put,get,delete` runs as a sequence so each key is written, read, then removed. `get` and `delete` on a key that was never written return an S3 error, so include `put` first.

The script checks the JSON body's `ok` field rather than only the HTTP status code. `S3StorageView` deliberately swallows backend errors and answers HTTP 200 with `ok: false`, so an HTTP-only check would report 100% success against a dead MinIO. A `status` of `simulated` additionally means botocore was not importable in the app, so no boto spans were emitted at all.

Boto spans are named `S3.<Operation>` and are rewritten by the collector to `normalized.service = "aws-s3"`, so they appear under the `aws-s3` service in the generic dashboards. Search for them with:

```traceql
{ name =~ "S3.*" }
```

## Verify the SDK

```bash
.venv/bin/python -m pytest -q
```

- **Local Unit Tests (`pytest`)**: Runs hermetic test suites using in-memory SQLite and in-memory span exporters, covering parent-child span waterfalls, route normalization, PII sanitization, sampler rules, error recording, and clean uninstrumentation.
- **End-to-End Integration Testing**: Full topologies (PostgreSQL primary/replica routing, PgBouncer pooling, Redis pipelines, live OTel Collector spanmetrics, Prometheus, and Grafana waterfalls) are verified via `docker compose up --build`.

## Scope and capabilities
 
- **Supported Frameworks & Storage**: Full auto-instrumentation for Django (WSGI/synchronous), PostgreSQL (primary & replica routing, `psycopg2`), PgBouncer connection pooling, Redis caching & pipelines, HTTP client (`requests`), and AWS Boto3 SDK.
- **Dashboards**: 4 pre-provisioned Grafana dashboards — Needs Attention (triage), Service Catalog, and two **generic** service/operation dashboards that cover any instrumented service. Django, PostgreSQL, PgBouncer, and Redis are values of a `$service` variable, not separate dashboards, so new services are picked up automatically.
- **Out of Current Scope**: ASGI / async views / async middleware, Celery background tasks, streaming HTTP responses, and function-based middleware.
