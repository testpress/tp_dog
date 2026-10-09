# tp_trace

`tp_trace` is a lightweight, drop-in Python observability SDK built on OpenTelemetry. It provides distributed tracing and request waterfalls for Django, PostgreSQL, Redis, Boto3/S3, and outgoing HTTP requests, exporting standard OTLP traces directly to an OpenTelemetry Collector.

---

## Installation

Install directly from GitHub:

```bash
pip install git+https://github.com/testpress/tp_trace.git
```

---

## Quickstart

Add `tp_trace.init()` to your Django application's `settings.py`:

```python
import tp_trace

tp_trace.init()
```

That's it. Calling `tp_trace.init()` automatically detects and instruments:
- **Django**: Request waterfall, middleware, views, and template rendering
- **Database**: PostgreSQL (`psycopg2`) cursor queries
- **Cache**: Redis commands and pipelines
- **AWS / S3**: Boto3 API calls
- **HTTP Client**: Outgoing calls made via `requests

---

## Testing & Verification

Run the test suite:

```bash
uv run pytest
```
