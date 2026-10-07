# Sample Django Application (tp_dog Demo)

This is the reference Django demonstration application instrumented with the **tp_dog OpenTelemetry SDK**. It provides endpoints that exercise PostgreSQL (primary and read replicas), PgBouncer connection pooling, Redis caching & pipelines, external HTTP calls, AWS S3/MinIO storage, and error scenarios.

---

## Architecture Overview

```text
                   ┌─────────────┐
                   │    Nginx    │
                   │ (Port 8001) │
                   └──────┬──────┘
                          │
                   ┌──────▼──────┐
                   │   Django    │ (tp_dog SDK auto-instruments)
                   └──────┬──────┘
        ┌─────────────┼─────────────┬─────────────┬─────────────┐
        │             │             │             │             │
 ┌──────▼──────┐┌─────▼──────┐┌─────▼──────┐┌─────▼──────┐┌─────▼──────┐┌──────────────┐
 │  pgbouncer  ││  slave1db  ││  slave2db  ││  slave3db  ││   redis    ││ external-api │
 │ (Port 6432) ││ (Port 5434)││ (Port 5435)││ (Port 5436)││(Port 6379) ││ (Port 8080)  │
 └──────┬──────┘└────────────┘└────────────┘└────────────┘└────────────┘└──────────────┘
 ┌──────▼──────┐
 │  postgres   │ (Port 5433)
 └─────────────┘
```

---

## Running with Docker Compose

To start the sample app along with the full observability stack (OTel Collector, Tempo, Prometheus, and Grafana):

```bash
# From workspace root
docker compose up --build
```

Access the services:
* **Web UI (Nginx)**: [http://localhost:8001](http://localhost:8001)
* **API Explorer**: [http://localhost:8001/](http://localhost:8001/)
* **Grafana APM**: [http://localhost:3000](http://localhost:3000) (User: `admin` / Password: `admin`)
* **Prometheus**: [http://localhost:9090](http://localhost:9090)
* **Tempo API**: [http://localhost:3200](http://localhost:3200)

---

## Demonstration Endpoints

| Method | Endpoint | Target Topology Component | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/products/` | `default` (PostgreSQL Primary) | Creates product in primary DB via PgBouncer |
| `GET` | `/api/products/read-slave1/` | `slave1` (Postgres Slave 1) | Reads products from slave1 DB via PgBouncer |
| `GET` | `/api/products/read-slave2/` | `slave2` (Postgres Slave 2) | Direct queries to slave2 DB |
| `GET` | `/api/products/read-slave3/` | `slave3` (Postgres Slave 3) | Direct queries to slave3 DB |
| `GET` | `/api/products/cache/` | `Redis` | Tests cache set, get, and hit/miss tracking |
| `GET` | `/api/products/external/` | `HTTP Client` (external-api) | Makes outbound HTTP call to httpbin |
| `GET` | `/api/products/s3-storage/` | `AWS Boto3` (MinIO) | Exercises S3 bucket list and object uploads |
| `GET` | `/api/products/error/` | Error Waterfall | Triggers a 500 error for trace validation |
| `GET` | `/api/products/slow/` | Latency Injection | Introduces synthetic 2s delay for latency analysis |
| `GET` | `/products-tmpl/` | HTML Rendering | Renders server-side Django template |

---

## How tp_dog is Integrated

Telemetry is initialized once in `config/settings.py` by calling `setup_telemetry()` from `config/otel.py`:

```python
# config/otel.py
import tp_dog

tp_dog.init(
    project_name="otel-sample",
    cluster_name="demo-cluster",
    tags={
        "server_location": "us-east-1",
        "team": "core-backend",
    },
    sample_rate=1.0,
)
```

Because `auto_patch=True` is the default, tp_dog automatically hooks:
* **Django**: Inbound HTTP requests, view execution, middleware, and templates.
* **PostgreSQL (`psycopg2`)**: Cursor queries, sanitized SQL, and PgBouncer connection identification.
* **Redis (`django_redis` / `redis-py`)**: Cache operations and pipeline executions.
* **HTTP Client (`requests`)**: Outbound HTTP requests with W3C `traceparent` propagation.
* **Boto3**: S3 object storage operations.