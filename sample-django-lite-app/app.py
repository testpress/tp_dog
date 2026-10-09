"""Standalone Pure Django Application (Zero Postgres, Zero Redis).

Instrumented with tp_trace OpenTelemetry SDK to demonstrate that the
catalog and overview dashboards dynamically display ONLY Django when no
downstream databases or caches are used.
"""

import os
import random
import time
from django.conf import settings
from django.http import JsonResponse, HttpResponseServerError
from django.urls import path
from django.core.wsgi import get_wsgi_application
import tp_trace

# 1. Initialize tp_trace SDK (auto_patch=True is the default)
tp_trace.init(
    project_name=os.getenv("TP_TRACE_PROJECT_NAME", os.getenv("TP_DOG_PROJECT_NAME", "django-lite-app")),
    cluster_name=os.getenv("TP_TRACE_CLUSTER_NAME", os.getenv("TP_DOG_CLUSTER_NAME", "done")),
    tags={
        "server_location": "ap-south-1",
        "team": "frontend-api",
        "cluster_name": "cluset1",
        "test_1": 1111,
        
    },
    environment=os.getenv("TP_TRACE_ENVIRONMENT", os.getenv("TP_DOG_ENVIRONMENT", "production")),
    endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://tp-otel-collector:4318"),
)

# 2. Configure lightweight Django settings with zero DB/Cache dependencies
if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="django-lite-pure-memory-poc-key",
        ROOT_URLCONF=__name__,
        ALLOWED_HOSTS=["*"],
        MIDDLEWARE=[
            "django.middleware.common.CommonMiddleware",
        ],
    )


# 3. View Handlers (Pure in-memory, no Postgres, no Redis)
def root_view(request):
    return JsonResponse({
        "status": "ok",
        "service": "django-lite-app",
        "dependencies": "none (pure django)",
        "time": time.time()
    })


def users_view(request):
    return JsonResponse([
        {"id": 1, "username": "alice", "role": "admin"},
        {"id": 2, "username": "bob", "role": "engineer"},
        {"id": 3, "username": "charlie", "role": "designer"}
    ], safe=False)


def orders_view(request):
    return JsonResponse([
        {"id": 101, "item": "Standard Plan", "price": 19.99, "status": "active"},
        {"id": 102, "item": "Enterprise Plan", "price": 199.99, "status": "paid"}
    ], safe=False)


def slow_view(request):
    delay = random.uniform(0.12, 0.28)
    time.sleep(delay)
    return JsonResponse({"status": "slow_response", "delay_ms": round(delay * 1000, 1)})


def error_view(request):
    return HttpResponseServerError(
        '{"error": "Simulated pure Django internal server error"}',
        content_type="application/json"
    )


# 4. URL Routes
urlpatterns = [
    path("", root_view, name="root"),
    path("api/users/", users_view, name="users"),
    path("api/orders/", orders_view, name="orders"),
    path("api/slow/", slow_view, name="slow"),
    path("api/error/", error_view, name="error"),
]

# 5. WSGI Application entrypoint
application = get_wsgi_application()
