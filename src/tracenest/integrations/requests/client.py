"""Outgoing HTTP request tracing wrapper for the `requests` library."""

import logging
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

from opentelemetry.trace import StatusCode

from tracenest.config import SDKConfig
from tracenest.sanitize import sanitize_url
from tracenest.route_context import get_current_route
from tracenest.safety import attempt
import tracenest

logger = logging.getLogger("tracenest.integrations.requests")

_config: Optional[SDKConfig] = None


def set_config(config: Optional[SDKConfig]) -> None:
    """Set the active SDKConfig for this module via the integration seam."""
    global _config
    _config = config


def _get_config() -> Optional[SDKConfig]:
    """Return the config received through the integration seam, or global fallback."""
    return _config if _config is not None else tracenest.get_config()


def _extract_request_meta(request: Any) -> Tuple[str, str, str, str, int, str]:
    """
    Extract method, sanitized URL, scheme, hostname, port, and peer_service from PreparedRequest.
    
    Returns:
        (method, sanitized_url, scheme, hostname, port, peer_service)
    """
    raw_method = getattr(request, "method", "GET") or "GET"
    method = str(raw_method).upper()

    raw_url = getattr(request, "url", "") or ""
    sanitized = sanitize_url(raw_url, strip_query=False)

    try:
        parsed = urlparse(sanitized)
        scheme = parsed.scheme or "http"
        hostname = parsed.hostname or "localhost"
        if parsed.port:
            port = parsed.port
        else:
            port = 443 if scheme == "https" else 80
    except Exception:
        scheme = "http"
        hostname = "localhost"
        port = 80

    peer_service = hostname
    return method, sanitized, scheme, hostname, port, peer_service


def _is_telemetry_request(sanitized_url: str, hostname: str, port: int) -> bool:
    """Check if the outgoing HTTP request is an OTLP export call.

    Deliberately conservative: telemetry detection is based on the collector's
    host/port only, never on the URL path. Otherwise any third-party API whose
    path contains /v1/traces (or similar) would be mistaken for the collector
    and its real response replaced with a fabricated 503.
    """
    if "otel-collector" in hostname or "tp-otel-collector" in hostname:
        return True
    if port in (4317, 4318) and hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        return True

    # Check against active TraceNest configuration
    try:
        cfg = _get_config()
        if cfg:
            if cfg.traces_endpoint and cfg.traces_endpoint in sanitized_url:
                return True
            if cfg.endpoint and cfg.endpoint in sanitized_url:
                return True
    except Exception:
        pass

    return False


def _apply_request_attributes(span: Any, request: Any) -> None:
    """Set TraceNest attributes on an outgoing requests span. May raise."""
    method, sanitized_url, scheme, hostname, port, peer_service = _extract_request_meta(request)
    parsed = urlparse(sanitized_url)
    clean_target = f"{scheme}://{hostname}{parsed.path or ''}"

    span.set_attribute("http.request.method", method)
    span.set_attribute("http.method", method)
    span.set_attribute("url.full", sanitized_url)
    span.set_attribute("http.url", sanitized_url)
    span.set_attribute("url.scheme", scheme)
    span.set_attribute("server.address", hostname)
    span.set_attribute("server.port", port)
    span.set_attribute("net.peer.name", hostname)
    span.set_attribute("net.peer.port", port)
    span.set_attribute("peer.service", peer_service)
    span.set_attribute("resource.name", f"{method} {clean_target}")

    # Tag the outgoing call with the in-flight Django endpoint so
    # per-endpoint spanmetrics series (http_route label) include it.
    route = get_current_route()
    if route:
        span.set_attribute("http.route", route)

    if hasattr(span, "update_name"):
        span.update_name(f"🌐 HTTP {method} {hostname}")


def tracenest_request_hook(span: Any, request: Any) -> None:
    """Request hook for RequestsInstrumentor to sanitize URLs and set TraceNest attributes.

    OTel's requests instrumentor calls this hook *before* ``wrapped_send`` and
    does not guard it, so a fault here would block the outgoing HTTP call
    entirely. The whole body therefore sits behind a single guard: losing the
    attributes is acceptable, losing the request is not.
    """
    if span is None or not getattr(span, "is_recording", lambda: True)():
        return

    attempt(_apply_request_attributes, span, request, _label="request_hook")


def _apply_response_attributes(span: Any, response: Any) -> None:
    """Set status and error attributes on a requests span. May raise."""
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        return

    status_code = int(status_code)
    span.set_attribute("http.response.status_code", status_code)
    span.set_attribute("http.status_code", status_code)

    if status_code >= 400:
        span.set_attribute("error", True)
        span.set_attribute("error.type", f"HTTP{status_code}")
        span.set_status(StatusCode.ERROR, description=f"HTTP {status_code}")


def tracenest_response_hook(span: Any, request: Any, response: Any) -> None:
    """Response hook for RequestsInstrumentor to set response status code and error flags.

    The instrumentor calls this outside its own try/except, so a fault here
    would raise in the application *after* the request already succeeded.
    """
    if span is None or not getattr(span, "is_recording", lambda: True)():
        return

    attempt(_apply_response_attributes, span, response, _label="response_hook")


