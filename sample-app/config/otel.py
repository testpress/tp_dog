"""TraceNest OpenTelemetry bootstrap for the Django POC sample app.

Initializes the TraceNest SDK with a single init() call. When auto_patch=True
(the default), all installed integrations (Django, PostgreSQL, Redis, etc.)
are automatically detected and patched.

Fail-safe: the app keeps working without the SDK installed.
"""
import logging
import os

logger = logging.getLogger("config.otel")


def setup_telemetry():
    """Initialize TraceNest with auto-patching. Idempotent, fail-safe."""
    if os.environ.get("TRACENEST_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("TraceNest disabled via TRACENEST_DISABLED")
        return False
    try:
        import tracenest
    except ImportError:
        logger.info("TraceNest not installed; observability disabled")
        return False
    try:
        # Minimal init — auto_patch=True (default) detects all installed integrations.
        # Service name, environment, endpoint all resolve from env vars:
        #   TRACENEST_SERVICE_NAME / OTEL_SERVICE_NAME
        #   TRACENEST_ENVIRONMENT / OTEL_ENVIRONMENT
        #   TRACENEST_ENDPOINT / OTEL_EXPORTER_OTLP_ENDPOINT
        # Or auto-detected from Django settings if configured there.
        tracenest.init(
            project_name="otel-sample",
            cluster_name="Dummy",
            tags={
                "server_location": "us-east-1",
                "team": "core-backend",
                "test": "HEHE"
            },
            sample_rate=1.0,
            endpoint_sample_rules={
                "/api/s3-storage/": 1.0,
            },
        )
        logger.info("TraceNest initialized with auto-patching")
        return True
    except Exception:
        logger.exception("TraceNest initialization failed; continuing without observability")
        return False
