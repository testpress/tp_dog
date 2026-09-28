"""Django request-response tracing middleware / wrapper for BaseHandler.get_response."""

import logging
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from opentelemetry.trace import SpanKind, StatusCode, get_tracer
from opentelemetry.propagate import extract

from tracenest.config import SDKConfig
from tracenest.safety import attempt, safe_enrich, safe_record_exception, safe_set_attribute, safe_set_status
from tracenest.sanitize import sanitize_url
from tracenest.tracing import reentrant_guard
from tracenest.route_context import reset_request_route, set_request_route
import tracenest

logger = logging.getLogger("tracenest.integrations.django")

_config: Optional[SDKConfig] = None


def set_config(config: Optional[SDKConfig]) -> None:
    """Set the active SDKConfig for this module via the integration seam."""
    global _config
    _config = config


def _get_config() -> Optional[SDKConfig]:
    """Return the config received through the integration seam, or global fallback."""
    return _config if _config is not None else tracenest.get_config()


def _apply_custom_tags(span, request=None, on_request_span=None):
    """Apply static tags and the user callback. Self-contained: never raises.

    Runs before the application call, so the config lookup is guarded here
    rather than at each call site.
    """
    cfg = attempt(_get_config, default=None, _label="request_config")
    if cfg and cfg.tags:
        safe_enrich(span, cfg.tags)

    callback = on_request_span or getattr(cfg, "on_request_span", None)
    if callback and request is not None:
        attempt(callback, span, request, _label="on_request_span")


_PARAM_REGEX = re.compile(r"<(?:[a-zA-Z0-9_]+:)?([a-zA-Z0-9_]+)>")
_TRAILING_SLASH = re.compile(r"/+$")
_MULTIPLE_SLASHES = re.compile(r"/+")


def _format_trace_id(trace_id_int: int) -> str:
    """Format 128-bit integer trace_id to 32-hex string."""
    return f"{trace_id_int:032x}"


def _format_span_id(span_id_int: int) -> str:
    """Format 64-bit integer span_id to 16-hex string."""
    return f"{span_id_int:016x}"


def _clean_regex_pattern(pattern: str) -> str:
    """Clean regex url pattern down to normalized route template."""
    p = str(pattern).lstrip("^").rstrip("$")
    p = re.sub(r"\(\?P<([^>]+)>[^\)]+\)", r"<\1>", p)
    p = _PARAM_REGEX.sub(r"<\1>", p)
    # Clean up escaped slashes or dots: \/ -> /, \. -> .
    p = p.replace(r"\/", "/").replace(r"\.", ".")

    # Clean up any leftover regex syntax (e.g. \w+, [0-9]+)
    p = re.sub(r"\[[^\]]+\]\+?", "*", p)
    p = re.sub(r"\\d\+?", "*", p)
    p = re.sub(r"\\w\+?", "*", p)

    if p and not p.startswith("/"):
        p = f"/{p}"
    return p


def _normalize_route(request, fallback_path: str) -> str:
    """Low-cardinality http.route from resolver_match, never raw URL with IDs."""
    resolver_match = getattr(request, "resolver_match", None)
    route = None
    if resolver_match:
        if getattr(resolver_match, "route", None):
            raw_route = str(resolver_match.route)
            if "^" in raw_route or "(?P<" in raw_route or "\\" in raw_route or "$" in raw_route:
                route = _clean_regex_pattern(raw_route)
            else:
                route = raw_route
        elif hasattr(resolver_match, "_urlpattern") and hasattr(resolver_match._urlpattern, "pattern"):
            try:
                pat = resolver_match._urlpattern.pattern  # type: ignore
                raw_pattern = pat.regex.pattern if hasattr(pat, "regex") else str(pat)
                route = _clean_regex_pattern(raw_pattern)
            except Exception:
                pass
        elif getattr(resolver_match, "url_name", None):
            route = resolver_match.url_name
    if not route:
        route = fallback_path
    route_str = str(route)
    if route_str and not route_str.startswith("/"):
        route_str = f"/{route_str}"
    return route_str


def _preresolve_route(path: str) -> str:
    """Best-effort normalized route resolved BEFORE the handler runs.

    Child spans (DB, cache, outgoing HTTP, S3) end before the SERVER span
    learns its route from ``resolver_match``; they read the route published
    from here via context vars instead. Falls back to the raw path when the
    URLconf cannot resolve it (unmatched URL, Django not fully set up).
    """
    fallback = path if path.startswith("/") else f"/{path}"
    try:
        from django.urls import resolve

        match = resolve(path)
        route = getattr(match, "route", None) or getattr(match, "url_name", None) or path
        route_str = str(route)
        if "^" in route_str or "(?P<" in route_str or "\\" in route_str or "$" in route_str:
            route_str = _clean_regex_pattern(route_str)
        if route_str and not route_str.startswith("/"):
            route_str = f"/{route_str}"
        return route_str
    except Exception:
        return fallback


def _resolve_view_name(request, method: str) -> str:
    resolver_match = getattr(request, "resolver_match", None)
    if not resolver_match:
        return "view"
    view_func = getattr(resolver_match, "func", None)
    cls = None
    if view_func is not None:
        cls = getattr(view_func, "cls", getattr(view_func, "view_class", None))
    actions = getattr(view_func, "actions", {}) if view_func else {}
    req_method = method.lower()
    if cls and isinstance(actions, dict) and req_method in actions:
        return f"{cls.__name__}.{actions[req_method]}"
    elif cls:
        return cls.__name__
    elif hasattr(resolver_match, "_func_path"):
        return str(resolver_match._func_path).split(".")[-1]
    elif view_func and hasattr(view_func, "__name__"):
        return str(view_func.__name__)
    elif getattr(resolver_match, "view_name", None):
        return str(resolver_match.view_name)
    return "view"


def traced_get_response(wrapped, instance, args, kwargs):
    """Wrapper for BaseHandler.get_response — SERVER span + metrics."""
    request = args[0] if args else kwargs.get("request")
    if request is None:
        return wrapped(*args, **kwargs)

    # Re-entrancy guard: same request object can be re-entered if middleware
    # calls get_response recursively. The flag lives on the request object
    # (per-request, not global), preventing nested SERVER spans for the same
    # logical request.
    with reentrant_guard(request, "_tp_traced_request") as should_trace:
        if not should_trace:
            return wrapped(*args, **kwargs)

        tracer = get_tracer("tracenest.django")
        method = getattr(request, "method", "GET").upper()
        path = getattr(request, "path", "/")
        scheme = getattr(request, "scheme", "http")

        # W3C propagation
        carrier: Dict[str, str] = {}
        if hasattr(request, "headers"):
            try:
                carrier.update({k.lower(): str(v) for k, v in request.headers.items()})
            except Exception:
                pass
        elif hasattr(request, "META") and isinstance(request.META, dict):
            for k, v in request.META.items():
                if k.startswith("HTTP_"):
                    carrier[k[5:].replace("_", "-").lower()] = str(v)
                elif k in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                    carrier[k.replace("_", "-").lower()] = str(v)

        parent_ctx = extract(carrier)

        try:
            raw_url = request.build_absolute_uri() if hasattr(request, "build_absolute_uri") else path
        except Exception:
            raw_url = path
        sanitized = sanitize_url(raw_url)
        url_path = path
        url_query = ""
        try:
            parsed = urlparse(sanitized)
            url_path = parsed.path or path
            url_query = parsed.query
        except Exception:
            pass

        ua = carrier.get("user-agent", "")
        span_attrs: Dict[str, Any] = {
            "span.type": "web",
            "component": "django",
            "http.request.method": method,
            "http.method": method,
            "url.full": sanitized,
            "url.path": url_path,
            "http.target": url_path,
            "url.scheme": scheme,
            "user_agent.original": ua,
            "http.user_agent": ua,
        }
        if url_query:
            span_attrs["url.query"] = url_query
        if hasattr(request, "META") and isinstance(request.META, dict):
            remote_ip = request.META.get("REMOTE_ADDR")
            xff = request.META.get("HTTP_X_FORWARDED_FOR")
            client_ip = xff.split(",")[0].strip() if xff else remote_ip
            if client_ip:
                span_attrs["client.address"] = str(client_ip)
                span_attrs["http.client_ip"] = str(client_ip)
            remote_port = request.META.get("REMOTE_PORT")
            if remote_port:
                try:
                    span_attrs["client.port"] = int(remote_port)
                except Exception:
                    pass
            host = request.META.get("HTTP_HOST") or request.META.get("SERVER_NAME")
            if host:
                span_attrs["server.address"] = str(host).split(":")[0]
            server_proto = request.META.get("SERVER_PROTOCOL")
            if server_proto:
                span_attrs["network.protocol.version"] = str(server_proto)
                span_attrs["http.flavor"] = str(server_proto)

        preresolved_route = _preresolve_route(path)
        if preresolved_route:
            span_attrs["http.route"] = preresolved_route

        start = time.monotonic()

        # Publish the pre-resolved route so child spans started inside the
        # handler carry http.route even though resolver_match is only
        # populated after the handler returns.
        route_token = set_request_route(preresolved_route, method)
        try:
            with tracer.start_as_current_span(
                "django.request",
                context=parent_ctx,
                kind=SpanKind.SERVER,
                attributes=span_attrs,
            ) as span:
                span_ctx = span.get_span_context()
                trace_id_hex = _format_trace_id(span_ctx.trace_id)
                span_id_hex = _format_span_id(span_ctx.span_id)
                request._tp_span = span
                request.trace_id = trace_id_hex
                request.span_id = span_id_hex
                _apply_custom_tags(span, request)

                status_code = 200
                error = False
                route_for_metrics = path
                # The application call is isolated from all telemetry enrichment.
                # An exception here is the application's own and must propagate.
                # Enrichment happens afterwards and can never turn a successful
                # response into an error, nor raise into the host application.
                try:
                    response = wrapped(*args, **kwargs)
                except Exception as exc:
                    error = True
                    status_code = 500
                    route_for_metrics = attempt(
                        _normalize_route, request, path, default=path, _label="normalize_route"
                    )
                    safe_set_attribute(span, "http.route", route_for_metrics)
                    safe_set_attribute(span, "http.response.status_code", 500)
                    safe_set_attribute(span, "http.status_code", 500)
                    safe_set_attribute(span, "error", True)
                    safe_set_attribute(span, "error.type", exc.__class__.__name__)
                    safe_record_exception(span, exc)
                    safe_set_status(span, StatusCode.ERROR, description=str(exc))
                    raise
                else:
                    status_code = attempt(
                        getattr, response, "status_code", default=200, _label="response.status_code"
                    )
                    norm_route = attempt(
                        _normalize_route, request, path, default=path, _label="normalize_route"
                    )
                    route_for_metrics = norm_route
                    view_name = attempt(
                        _resolve_view_name, request, method, default="view", _label="view_name"
                    )

                    safe_set_attribute(span, "http.route", norm_route)
                    safe_set_attribute(span, "http.response.status_code", status_code)
                    safe_set_attribute(span, "http.status_code", status_code)
                    safe_set_attribute(span, "django.view", str(view_name))
                    safe_set_attribute(span, "django.view.name", str(view_name))
                    safe_set_attribute(span, "resource.name", f"{method} {norm_route}")

                    def _set_phrase() -> None:
                        import http

                        phrase = http.HTTPStatus(status_code).phrase
                        safe_set_attribute(span, "http.status_text", phrase)
                        safe_set_attribute(span, "http.response.status_text", phrase)

                    attempt(_set_phrase, _label="status_text")

                    def _set_user() -> None:
                        user = getattr(request, "user", None)
                        if user and getattr(user, "is_authenticated", False):
                            user_id = str(getattr(user, "pk", getattr(user, "id", "")))
                            if user_id:
                                safe_set_attribute(span, "usr.id", user_id)
                                safe_set_attribute(span, "user.id", user_id)
                                safe_set_attribute(span, "enduser.id", user_id)
                            safe_set_attribute(span, "user.is_authenticated", True)

                    attempt(_set_user, _label="user_attrs")

                    if status_code >= 500:
                        error = True
                        safe_set_attribute(span, "error", True)
                        safe_set_attribute(span, "error.type", str(status_code))
                        safe_set_status(span, StatusCode.ERROR, description=f"HTTP {status_code}")
                    else:
                        safe_set_attribute(span, "error", False)
                        safe_set_status(span, StatusCode.OK)

                    if hasattr(response, "headers") or hasattr(response, "__setitem__"):
                        def _set_headers() -> None:
                            response["X-Trace-ID"] = trace_id_hex
                            response["X-Span-ID"] = span_id_hex
                            # W3C trace context so downstream consumers can correlate
                            # the response back to this trace without knowing our
                            # custom headers.
                            response["traceparent"] = f"00-{trace_id_hex}-{span_id_hex}-01"

                        attempt(_set_headers, _label="trace_headers")

                    return response
                finally:
                    duration_ms = (time.monotonic() - start) * 1000.0
                    safe_set_attribute(span, "http.request.duration_ms", duration_ms)
        finally:
            reset_request_route(route_token)
