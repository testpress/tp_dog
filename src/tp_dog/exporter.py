"""Fail-safe span exporter wrapper for tp_dog.

Guarantees that telemetry export failures (network errors, connection refused,
unreachable collectors, DNS failures, timeouts) NEVER crash or disrupt the host application.
"""

import logging
import time
from typing import Any, Optional
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger("tp_dog.exporter")


def _ensure_normalized_attributes(span: Any) -> None:
    try:
        attrs = getattr(span, "_attributes", None)
        if attrs is None:
            attrs = getattr(span, "attributes", None)
        if attrs is not None and hasattr(attrs, "__setitem__"):
            if not attrs.get("normalized.service"):
                svc = (
                    attrs.get("peer.service")
                    or attrs.get("db.system")
                    or attrs.get("rpc.system")
                    or attrs.get("component")
                )
                if svc == "postgresql":
                    svc = "postgres"
                elif svc == "aws-api":
                    svc = "aws-s3"
                attrs["normalized.service"] = str(svc or "django")

            if not attrs.get("normalized.operation"):
                op = None
                if attrs.get("http.method") and attrs.get("http.route"):
                    op = f"{attrs.get('http.method')} {attrs.get('http.route')}"
                elif attrs.get("db.statement"):
                    op = attrs.get("db.statement")
                elif attrs.get("rpc.service") and attrs.get("rpc.method"):
                    op = f"{attrs.get('rpc.service')}.{attrs.get('rpc.method')}"
                else:
                    op = getattr(span, "name", "")
                if op:
                    attrs["normalized.operation"] = str(op)
    except Exception:
        pass


class SafeSpanExporter(SpanExporter):
    """Fail-safe wrapper around any OpenTelemetry SpanExporter.

    If the collector is down, unreachable, or refusing connections (e.g. Errno 61),
    this wrapper absorbs the error, logs a rate-limited diagnostic warning, and returns
    SpanExportResult.FAILURE without allowing any exception to bubble up.
    """

    def __init__(self, exporter: SpanExporter, endpoint: Optional[str] = None):
        self._exporter = exporter
        self._endpoint = endpoint or getattr(exporter, "_endpoint", "collector")
        self._last_log_time = -float("inf")
        self._error_count = 0

    def export(self, spans: Any) -> SpanExportResult:
        try:
            if hasattr(spans, "__iter__"):
                for s in spans:
                    _ensure_normalized_attributes(s)
            res = self._exporter.export(spans)
            if res == SpanExportResult.SUCCESS and self._error_count > 0:
                logger.info("tp_dog: Connection to collector at %s restored.", self._endpoint)
                self._error_count = 0
            return res
        except Exception as exc:
            self._error_count += 1
            now = time.monotonic()
            # Rate-limit warnings: log first failure, then at most once every 5 minutes
            if now - self._last_log_time > 300:
                self._last_log_time = now
                logger.warning(
                    "tp_dog: Unable to export spans to collector at %s (%s). "
                    "Host application is completely unaffected. (Failures: %d)",
                    self._endpoint,
                    exc,
                    self._error_count,
                )
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        try:
            self._exporter.shutdown()
        except Exception:
            pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return self._exporter.force_flush(timeout_millis)
        except Exception:
            return False
