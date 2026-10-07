# Supported Components & Integrations

tp_dog automatically detects and instruments key frameworks, databases, and client libraries in your Python application.

When `tp_dog.init()` runs, it auto-patches all installed components with zero configuration.

```text
                                DJANGO REQUEST WATERFALL
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ django.request [SERVER]                                                  │
 │  ├── django.middleware.security.SecurityMiddleware.__call__           │
 │  ├── django.contrib.auth.middleware.AuthenticationMiddleware.__call__  │
 │  └── 🐍 django.view.ProductDetailView                                   │
 │       ├── 🔵 SELECT id, name, price FROM products WHERE id = ?          │
 │       ├── 🔴 django_redis.cache.get                                     │
 │       ├── 🌐 HTTP GET api.inventory.internal                            │
 │       └── 🎨 django.template: products/detail.html                       │
 └──────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Django Integration

The Django integration provides complete lifecycle observability for inbound HTTP requests.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`django.request`** | `SERVER` | Root span representing the entire HTTP request | `http.method`, `http.status_code`, `http.route`, `client.address` |
| **`<module>.<Class>.<method>`** | `INTERNAL` | Timing for middleware hooks (`__call__`, `process_request`, etc.) | `django.middleware`, `django.middleware.name`, `django.middleware.method` |
| **`🐍 django.view.<Name>`** | `INTERNAL` | View execution (FBVs, CBVs, DRF viewsets, `dispatch`) | `django.view`, `django.view.name`, `django.view.class`, `django.view.action` |
| **`🎨 django.template: <name>`** | `INTERNAL` | Template rendering and nested `{% include %}` | `django.template.name` |
| **`🔴 django_redis.cache.<op>`** | `INTERNAL` | Django cache backend calls (`get`, `set`, `delete`) | `django.cache.operation`, `django.cache.backend`, `django.cache.key`, `django.cache.hit` |
| **`🔐 django.auth.login`** | `INTERNAL` | User login event | `django.auth.action="login"`, `usr.id`, `enduser.id` |
| **`🔐 django.auth.authenticate`** | `INTERNAL` | User authentication attempts | `django.auth.action="authenticate"`, `auth.success` (bool), `usr.id`, `enduser.id` |

### Key Features
- **Route Normalization**: Automatically converts high-cardinality paths like `/api/users/8572/` into normalized templates `/api/users/{id}/` to keep Prometheus metric series clean.
- **Trace Context Propagation**: Extracts incoming W3C `traceparent` headers to connect upstream services to the Django trace.
- **Response Headers**: Injects `X-Trace-ID` and `X-Span-ID` into outgoing HTTP responses for easy frontend-to-backend correlation.

---

## 2. PostgreSQL Integration (`psycopg2`)

Wraps database cursor execution to track queries, timing, and connection topology.
Two seams are patched — Django's `execute_wrappers` and the database backend's
`CursorWrapper` — and they are mutually exclusive via OpenTelemetry's
`suppress_instrumentation` context, so a query produces exactly one span.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`🟢 <normalized_sql>`** | `CLIENT` | Direct PostgreSQL database query (e.g. `🟢 SELECT * FROM users WHERE id = ?`) | `db.system="postgresql"`, `db.statement`, `db.operation`, `db.role="primary"`, `db.instance` |
| **`🔹 <normalized_sql>`** | `CLIENT` | Query executed through **PgBouncer** connection pool | `db.system="postgresql"`, `db.statement`, `db.connection.pool="pgbouncer"` |
| **`🟢 postgres.query`** | `CLIENT` | Fallback span name when SQL statement is empty or unavailable | `db.system="postgresql"`, `db.role`, `db.instance` |

> **Span name vs. `db.statement`**: the span name and `db.statement` use the
> *metric-normalized* form (comments stripped, `IN (…)` arity collapsed, batch
> `VALUES` collapsed, bounded to 256 chars) to keep Prometheus cardinality low.
> The fuller sanitized text is available on `db.statement.full` and
> `db.query.text` (bounded to 4096 chars).

### Key Features
- **SQL Sanitization**: Strips literals, IDs, strings, and sensitive values (e.g. `SELECT * FROM users WHERE email = 'bob@example.com'` $\rightarrow$ `SELECT * FROM users WHERE email = ?`).
- **Topology Awareness**: Automatically distinguishes between Primary DB (`db.role="primary"`), Read-Replicas (`db.role="replica"`), and PgBouncer connection pools using host/port metadata.

---

## 3. Redis Integration (`redis-py` & `django_redis`)

Instruments Redis client commands and Django cache operations.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`🔸 <COMMAND>`** (e.g. `🔸 GET`, `🔸 SET`) | `CLIENT` | Direct low-level Redis client commands via `RedisInstrumentor` with custom hook | `db.system="redis"`, `db.operation`, `db.statement`, `net.peer.name` |
| **`🔸 PIPELINE`** | `CLIENT` | Redis batch pipeline execution | `db.system="redis"`, `db.operation="PIPELINE"` |
| **`🔸 django_redis.cache.<op>`** | `CLIENT` | Django cache operations (`get`, `set`, `delete_many`, etc.) | `django.cache.operation`, `django.cache.backend`, `django.cache.key`, `django.cache.hit` |

### Key Features
- **Sensitive Command Redaction**: Arguments for commands like `AUTH`, `CONFIG`, and `PASSWORD` are scrubbed. *This behaviour is inherited from the upstream `opentelemetry-instrumentation-redis` package — tp_dog registers no Redis hooks of its own, so the guarantees (and gaps) are that package's and move with its version.*
- **Network Safety**: Masks internal IP addresses and formats socket connection targets cleanly.

> **Known gaps**: RediSearch query arguments and document field values are
> emitted raw by the upstream instrumentor, and Django cache keys are exported
> verbatim on `django.cache.key` (unbounded for `get_many` / `delete_many`) —
> a PII and cardinality risk.

---

## 4. HTTP Client Integration (`requests`)

Instruments outbound HTTP requests made by your application to external APIs or internal microservices.

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`🌐 HTTP <METHOD> <HOST>`** | `CLIENT` | Outbound HTTP request (e.g. `HTTP GET api.stripe.com`) | `http.method`, `http.url`, `http.status_code`, `peer.service` |

### Key Features
- **Distributed Context Injection**: Automatically injects W3C `traceparent` headers into outgoing request headers so downstream services continue the exact same trace.
- **URL Sanitization**: Strips basic-auth credentials and sensitive query parameters from logged URLs.

---

## 5. AWS Boto3 Integration (`boto3`)

Instruments AWS SDK operations (S3, SQS, DynamoDB, or local MinIO/mock services).

| Span Name / Pattern | Kind | Description | Key Attributes |
| :--- | :--- | :--- | :--- |
| **`🪣 S3.<Operation>`** (e.g. `🪣 S3.ListObjectsV2`, `🪣 S3.PutObject`) | `CLIENT` | Amazon S3 / MinIO object storage operations | `rpc.system="aws-api"`, `rpc.service="s3"`, `rpc.method`, `aws.region` |
| **`☁️ <Service>.<Operation>`** | `CLIENT` | Other AWS SDK operations (e.g. SQS, DynamoDB) | `rpc.system="aws-api"`, `rpc.service`, `rpc.method` |

### Key Features
- Captures RPC method, target resource names (e.g., S3 bucket name), and response status codes.
- Distinguishes S3 bucket/object operations with `🪣` icon in trace waterfalls and flamegraphs.

---

## 6. Component Enable / Disable Matrix

All installed integrations are enabled by default (`auto_patch=True`). You can selectively disable any component during initialization:

```python
import tp_dog

tp_dog.init(
    project_name="my-django-service",
    integrations={
        "django": True,      # Django request/views/middleware
        "postgres": True,    # PostgreSQL queries
        "redis": False,      # Disable Redis tracing
        "requests": True,    # Outbound HTTP calls
        "boto": False,       # Disable AWS SDK tracing
    }
)
```
