"""Propagate the normalized Django ``http.route`` to child spans.

Problem: the SERVER span (``django.request``) only learns its normalized
``http.route`` *after* the wrapped handler returns, i.e. after all child
spans (PostgreSQL, Redis, outgoing HTTP, S3) have already ended. Child spans
therefore carry no ``http.route`` attribute, and the spanmetrics connector
cannot emit per-endpoint (``http_route``-labelled) series for them. Endpoint
dashboards that divide per-endpoint downstream time by per-endpoint request
time then compare global numerators against per-endpoint denominators and
report percentages far above 100%.

Fix: :func:`traced_get_response` pre-resolves the route from the URLconf
*before* invoking the handler and publishes it via :data:`current_route` /
:data:`current_method` context variables. :func:`traced_span` and
:class:`RouteEnrichingSpanProcessor` copy those values onto every span
started while the request is in flight (including spans created by upstream
OpenTelemetry instrumentors such as botocore, which never pass through
:func:`traced_span`).
"""

import contextvars
from typing import Any, Optional, Tuple

from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.trace import Span

current_route: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tracenest.http_route", default=None
)
current_method: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "tracenest.http_method", default=None
)


def get_current_route() -> Optional[str]:
    """Return the normalized route of the in-flight Django request, if any."""
    try:
        return current_route.get()
    except LookupError:
        return None


def get_current_method() -> Optional[str]:
    """Return the HTTP method of the in-flight Django request, if any."""
    try:
        return current_method.get()
    except LookupError:
        return None


def set_request_route(route: Optional[str], method: Optional[str] = None) -> Tuple[Any, Any]:
    """Publish route/method for child spans; returns tokens for :func:`reset_request_route`."""
    return current_route.set(route), current_method.set(method)


def reset_request_route(tokens: Optional[Tuple[Any, Any]]) -> None:
    """Reset route/method context vars using tokens from :func:`set_request_route`."""
    if not tokens:
        current_route.set(None)
        current_method.set(None)
        return
    try:
        route_token, method_token = tokens
    except Exception:
        current_route.set(None)
        current_method.set(None)
        return

    try:
        current_route.reset(route_token)
    except Exception:
        current_route.set(None)
    try:
        current_method.reset(method_token)
    except Exception:
        current_method.set(None)


class RouteEnrichingSpanProcessor(SpanProcessor):
    """Copy the in-flight request route and global tags onto spans.

    Covers spans created by upstream OpenTelemetry instrumentors (botocore,
    requests) that never pass through :func:`tracenest.tracing.traced_span`.
    Runs on span start so attributes are present no matter which export
    processor ends the span.
    """

    def __init__(self, static_attributes: Optional[dict] = None) -> None:
        self.static_attributes = dict(static_attributes or {})

    def on_start(self, span: Span, parent_context: Optional[Context] = None) -> None:
        try:
            for k, v in self.static_attributes.items():
                if v is not None:
                    span.set_attribute(k, v)

            existing = getattr(span, "attributes", None) or {}
            if not existing.get("http.route"):
                route = get_current_route()
                if route and route != "__unmatched__":
                    span.set_attribute("http.route", route)
            method = get_current_method()
            if method:
                if not existing.get("http.request.method"):
                    span.set_attribute("http.request.method", method)
                if not existing.get("http.method"):
                    span.set_attribute("http.method", method)
        except Exception:
            pass

    def on_end(self, span: ReadableSpan) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
